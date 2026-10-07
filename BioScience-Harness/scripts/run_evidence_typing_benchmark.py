#!/usr/bin/env python
"""Evidence typing on real abstracts: how often the rules read the design PubMed gives.

    # the committed benchmark
    python scripts/run_evidence_typing_benchmark.py --out benchmarks/evidence_typing

    # CI: recompute offline and refuse a regression against the committed results
    python scripts/run_evidence_typing_benchmark.py \\
        --check benchmarks/evidence_typing/results.json

    # another version of the rules on the same sample, e.g. an earlier commit's
    git show 1e14a2a:BioScience-Harness/src/bioagent/literature/evidence.py > before.py
    python scripts/run_evidence_typing_benchmark.py --rules before.py --out RUN/before

    # the filled fields to check by hand: a seeded draw from the test split
    python scripts/run_evidence_typing_benchmark.py --draw-manual-check 30

The sample, its labels and its split are frozen in ``benchmarks/evidence_typing``
(``scripts/freeze_evidence_typing_sample.py`` wrote them, from Europe PMC and PubMed). This
script reads them offline and refuses a sample whose labels or split are not the ones its
metadata and identifiers give (``bioagent.benchmarks.evidence_typing.load_sample``).

A regression is a record read worse than the committed results show (correct to
unassessed or wrong, unassessed to wrong; on a negative, any design is wrong), or a field
the manual check found correct that now reads otherwise. Improvements pass and ask for the
results to be regenerated, so the committed numbers stay the ones the code produces.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap

_bootstrap.bootstrap()

from bioagent.benchmarks.evidence_typing import (  # noqa: E402
    DESIGNS, compare, draw_manual_check, load_rules, load_sample, manual_check_summary,
    render_markdown, results, rules_digest, run, sample_digest,
)
from bioagent.literature.evidence import read_fields  # noqa: E402

SAMPLE = _bootstrap.ROOT / "benchmarks" / "evidence_typing"


def _optional(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _line(split: dict) -> str:
    cells = [f"{d} {split['designs'][d]['recall']['k']}/{split['designs'][d]['recall']['n']}"
             for d in DESIGNS]
    typed = split["negatives_typed"]["all"]["typed"]
    return (f"correct {', '.join(cells)}; wrong {split['wrong']}; negatives typed "
            f"{typed['k']}/{typed['n']}")


def _check(path: Path, sample: Path) -> int:
    committed = json.loads(path.read_text(encoding="utf-8"))
    records, labels = load_sample(sample)
    now = results(run(records, labels), sample=sample_digest(sample),
                  rules=rules_digest(read_fields))
    problems, notes = compare(committed, now)
    if committed["sample_sha256"] == now["sample_sha256"]:
        print(f"test now:       {_line(now['splits']['test'])}")
        print(f"test committed: {_line(committed['splits']['test'])}")
    for note in notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"REGRESSION: {problem}", file=sys.stderr)
    return 1 if problems else 0


def _draw(sample: Path, n: int) -> int:
    """The fields to check, each with its abstract and the span marked, one JSON per line."""
    records, labels = load_sample(sample)
    by_id = {r.pmid: r for r in records}
    for entry in draw_manual_check(run(records, labels), n):
        record = by_id[entry["pmid"]]
        reading = read_fields(record.text)[entry["field"]]
        start, end = reading.offset, reading.offset + len(reading.span)
        print(json.dumps({**entry, "rule": reading.rule,
                          "text": f"{record.text[:start]}«{record.text[start:end]}»"
                                  f"{record.text[end:]}"}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", default=str(SAMPLE),
                    help="directory holding sample.jsonl and labels.jsonl")
    ap.add_argument("--out", default="", help="write results.json and results.md here")
    ap.add_argument("--check", default="", help="committed results.json to compare against")
    ap.add_argument("--rules", default="",
                    help="an evidence.py to measure instead of the package's")
    ap.add_argument("--draw-manual-check", type=int, default=0, metavar="N",
                    help="print N filled fields of the test split to check by hand")
    args = ap.parse_args(argv)
    sample = Path(args.sample)
    if args.check:
        return _check(Path(args.check), sample)
    if args.draw_manual_check:
        return _draw(sample, args.draw_manual_check)
    if not args.out:
        ap.error("give --out, --check or --draw-manual-check")
    read = load_rules(args.rules) if args.rules else read_fields
    records, labels = load_sample(sample)
    outcomes = run(records, labels, read)
    data = results(outcomes, sample=sample_digest(sample), rules=rules_digest(read))
    baseline = None
    if not args.rules:
        manual = _optional(sample / "manual_check.json")
        if manual is not None:
            try:
                data["manual_check"] = manual_check_summary(manual, outcomes)
            except ValueError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 1
        baseline = _optional(sample / "baseline.json")
        if baseline is not None:
            data["baseline"] = {"rules_sha256": baseline["rules_sha256"],
                                "splits": baseline["splits"]}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
    text = render_markdown(data, sampling=_optional(sample / "sampling.json"),
                           baseline=baseline)
    (out / "results.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {out / 'results.json'} and {out / 'results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
