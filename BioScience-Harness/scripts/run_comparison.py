#!/usr/bin/env python
"""The four-arm comparison (A, A+, B, C, D): the same model, sources and budget per arm.

    # check the harness end to end, offline: a scripted client on the fixture tasks
    python scripts/run_comparison.py selftest --out RUN/selftest

    # run an experiment: you name the model; there is no default
    python scripts/run_comparison.py run --tasks tasks.jsonl --model MODEL_ID \\
        --repeats 3 --out RUN/exp1

    # blinded review of the claims gold does not decide (keep key.jsonl to yourself)
    python scripts/run_comparison.py export --run RUN/exp1 --tasks tasks.jsonl

    # score, with the reviewers' file once it is back
    python scripts/run_comparison.py score --run RUN/exp1 --tasks tasks.jsonl \\
        --reviews RUN/exp1/review.jsonl

The protocol, the metrics and what a result needs are in docs/comparison.md. ``run`` calls
the Messages API through the ``anthropic`` SDK with the SDK's own credentials; it records
the model id, the request settings, the task file's hash and the arms in manifest.json, so
the run can be reported and repeated as it was made.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
from pathlib import Path

import _bootstrap

_bootstrap.bootstrap()

from bioagent.benchmarks.comparison import (  # noqa: E402
    ARMS, AnthropicClient, Budget, ScriptedClient, Transcript, export_for_review,
    fixture_script, fixture_tasks, load_reviews, load_tasks, render_markdown, run_comparison, score,
)


def _write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def _transcripts(run: Path) -> list[Transcript]:
    return [Transcript.from_dict(json.loads(line)) for line in
            (run / "transcripts.jsonl").read_text(encoding="utf-8").splitlines() if line]


def _tasks(args):
    return fixture_tasks() if args.fixture_tasks else load_tasks(args.tasks)


def _report(transcripts, tasks, out: Path, reviews=None, resamples=2000) -> str:
    report = score(transcripts, tasks, reviews=reviews, resamples=resamples)
    (out / "report.json").write_text(json.dumps(report.as_dict(), ensure_ascii=False,
                                                indent=1) + "\n", encoding="utf-8")
    text = render_markdown(report)
    (out / "report.md").write_text(text, encoding="utf-8")
    return text


def _selftest(args) -> int:
    """Every arm on the fixture tasks with a scripted client: the mechanics, not a result."""
    tasks = fixture_tasks()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    transcripts = run_comparison(tasks, list(ARMS), ScriptedClient(fixture_script()))
    _write_jsonl(out / "transcripts.jsonl", (t.as_dict() for t in transcripts))
    print(_report(transcripts, tasks, out, resamples=200))
    print(f"wrote {out}/transcripts.jsonl, report.json, report.md (not a result)")
    return 0


def _run(args) -> int:
    params = json.loads(args.params) if args.params else {}
    tasks = _tasks(args)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        raise SystemExit(f"unknown arm(s) {unknown}; arms are {list(ARMS)}")
    client = AnthropicClient(args.model, params=params)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        import anthropic
        sdk = getattr(anthropic, "__version__", "")
    except ImportError:                                           # pragma: no cover
        sdk = ""
    source = Path(args.tasks).read_bytes() if args.tasks else b"fixture_tasks()"
    manifest = {"model": args.model, "params": params, "arms": arms,
                "repeats": args.repeats, "max_tokens": args.max_tokens,
                "tasks": args.tasks or "fixture_tasks()",
                "tasks_sha256": hashlib.sha256(source).hexdigest(),
                "n_tasks": len(tasks), "anthropic_sdk": sdk,
                "started": _dt.datetime.now(_dt.timezone.utc).isoformat()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n",
                                       encoding="utf-8")
    transcripts = run_comparison(tasks, arms, client, Budget(max_tokens=args.max_tokens),
                                 repeats=args.repeats)
    _write_jsonl(out / "transcripts.jsonl", (t.as_dict() for t in transcripts))
    failed = sum(r == "error" for t in transcripts for r in t.stop_reasons)
    print(_report(transcripts, tasks, out))
    print(f"wrote {out}: {len(transcripts)} transcripts, {failed} failed call(s)")
    return 0


def _export(args) -> int:
    run = Path(args.run)
    n = export_for_review(_transcripts(run), _tasks(args), run / "review.jsonl",
                          run / "key.jsonl", seed=args.seed)
    print(f"wrote {n} item(s) to {run / 'review.jsonl'}; keep {run / 'key.jsonl'} from "
          "the reviewers until their verdicts are in")
    return 0


def _score(args) -> int:
    run = Path(args.run)
    reviews = load_reviews(args.reviews) if args.reviews else None
    print(_report(_transcripts(run), _tasks(args), run, reviews=reviews,
                  resamples=args.resamples))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    def tasks_args(p):
        p.add_argument("--tasks", default="", help="tasks, one JSON object per line")
        p.add_argument("--fixture-tasks", action="store_true",
                       help="the two synthetic fixture tasks (reported as not a result)")

    p = sub.add_parser("selftest", help="scripted client on the fixtures, offline")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_selftest)

    p = sub.add_parser("run", help="run the arms with a model you name")
    tasks_args(p)
    p.add_argument("--model", required=True,
                   help="the model id every arm uses; required, no default")
    p.add_argument("--params", default="",
                   help='further request settings as JSON, the same for every arm, e.g. '
                        '\'{"thinking": {"type": "adaptive"}}\'')
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--max-tokens", type=int, default=Budget().max_tokens)
    p.add_argument("--out", required=True)
    p.set_defaults(func=_run)

    p = sub.add_parser("export", help="write the blinded review file and its key")
    tasks_args(p)
    p.add_argument("--run", required=True)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=_export)

    p = sub.add_parser("score", help="score a run, with reviews when they are back")
    tasks_args(p)
    p.add_argument("--run", required=True)
    p.add_argument("--reviews", default="")
    p.add_argument("--resamples", type=int, default=2000)
    p.set_defaults(func=_score)

    args = ap.parse_args(argv)
    if args.command != "selftest" and not (args.tasks or args.fixture_tasks):
        ap.error("give --tasks FILE, or --fixture-tasks")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
