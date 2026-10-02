"""``python -m bioagent.spatial --config run.yaml --out results/`` (prints the summary)."""

from __future__ import annotations

import argparse
import json
import sys

from .io import SpatialInputError
from .pipeline import run_spatial


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bioagent.spatial")
    ap.add_argument("--config", required=True, help="JSON or YAML run configuration")
    ap.add_argument("--out", required=True, help="output directory")
    a = ap.parse_args(argv)
    try:
        summary = run_spatial(a.config, a.out)
    except SpatialInputError as exc:
        sys.stdout.write(json.dumps({"status": "input_error", "error": str(exc)}))
        return 2
    sys.stdout.write(json.dumps(summary, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
