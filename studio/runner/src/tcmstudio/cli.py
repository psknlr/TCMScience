"""``tcmstudio``: the catalog, one call, the local runner, and the web build.

    tcmstudio catalog [--where browser|runner] [--out FILE] [--no-probe]
    tcmstudio call TOOL --args JSON [--where browser|runner] [--network] [--project ID]
    tcmstudio serve …        the local runner (tcmstudio.server)
    tcmstudio webbuild …     the static site build (tcmstudio.webbuild)
    tcmstudio corpus build|fetch|info …   the published corpus (tcmstudio.corpus.cli)

``serve``, ``webbuild`` and ``corpus`` belong to their own modules; this file only hands them
their arguments, importing them when asked so the other commands never load them.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__

__all__ = ["main", "build_parser"]

_DELEGATED = {"serve": ("tcmstudio.server", "run the local runner on 127.0.0.1 (CPU/GPU jobs, "
                                            "connectors, data hub)"),
              "webbuild": ("tcmstudio.webbuild", "build the static web app with the catalog "
                                                 "and the Python bundle"),
              "corpus": ("tcmstudio.corpus.cli", "build, fetch (for offline use) or describe the "
                                                 "published TCM corpus")}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tcmstudio", description="TCMScience Studio: tool catalog, dispatcher and local runner.")
    p.add_argument("--version", action="version", version=f"tcmstudio {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    c = sub.add_parser("catalog", help="write the tool catalog (CONTRACTS §2) as JSON")
    c.add_argument("--where", choices=("runner", "browser"), default="runner",
                   help="the runtime the catalog describes (default: runner)")
    c.add_argument("--out", default="", help="file to write (default: standard output)")
    c.add_argument("--no-probe", action="store_true",
                   help="do not check which optional dependencies are installed")
    c.add_argument("--indent", type=int, default=None, help="pretty-print with this indent")

    k = sub.add_parser("call", help="run one tool and print its envelope (CONTRACTS §3)")
    k.add_argument("tool", help="a core tool name, call_tool, or a catalog entry id")
    k.add_argument("--args", default="{}",
                   help="arguments as JSON, @FILE to read them from a file, or - for stdin")
    k.add_argument("--where", choices=("runner", "browser"), default="runner")
    k.add_argument("--network", action="store_true",
                   help="allow network access (permission profile biomedical-research)")
    k.add_argument("--purpose", choices=("academic", "commercial"), default="academic")
    k.add_argument("--project", default=None, help="project id (selects the audit chain)")
    k.add_argument("--state-root", default=None,
                   help="directory holding each project's PSH state (default: a temporary "
                        "directory, not durable)")
    k.add_argument("--tcmdb-root", default=None, help="root of the TCM data hub")
    k.add_argument("--compact", action="store_true", help="print the envelope on one line")
    k.add_argument("--text", action="store_true", help="print only the model's text view")

    for name, (_, help_text) in _DELEGATED.items():
        d = sub.add_parser(name, help=help_text, add_help=False)
        d.add_argument("rest", nargs=argparse.REMAINDER)
    return p


def _read_args(spec: str) -> Any:
    if spec == "-":
        text = sys.stdin.read()
    elif spec.startswith("@"):
        text = Path(spec[1:]).read_text(encoding="utf-8")
    else:
        text = spec
    return json.loads(text) if text.strip() else {}


def _delegate(name: str, argv: Sequence[str]) -> int:
    module_name, _ = _DELEGATED[name]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        print(f"tcmstudio {name}: not available in this installation ({module_name} could not "
              f"be imported: {exc}).", file=sys.stderr)
        return 2
    add_arguments = getattr(module, "add_arguments", None)
    run = getattr(module, "run", None)
    if not callable(add_arguments) or not callable(run):
        print(f"tcmstudio {name}: {module_name} does not define add_arguments(parser) and "
              "run(args).", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog=f"tcmstudio {name}")
    add_arguments(parser)
    args = parser.parse_args(list(argv))
    code = run(args)
    return int(code) if isinstance(code, int) else 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _DELEGATED:
        return _delegate(argv[0], argv[1:])
    parser = build_parser()
    a = parser.parse_args(argv)
    if a.cmd is None:
        parser.print_help()
        return 2
    if a.cmd == "catalog":
        from .catalog import build_catalog
        doc = build_catalog(a.where, probe=not a.no_probe)
        text = json.dumps(doc, ensure_ascii=False, indent=a.indent,
                          separators=None if a.indent else (",", ":"))
        if a.out:
            out = Path(a.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text + "\n", encoding="utf-8")
            print(f"wrote {out} ({doc['counts']['core']} core tools, "
                  f"{doc['counts']['entries']} entries)", file=sys.stderr)
        else:
            sys.stdout.write(text + "\n")
        return 0 if not doc.get("problems") else 1
    if a.cmd == "call":
        from .dispatch import call
        try:
            arguments = _read_args(a.args)
        except (OSError, ValueError) as exc:
            print(f"tcmstudio call: --args is not JSON ({exc})", file=sys.stderr)
            return 2
        context = {"where": a.where, "network": a.network, "purpose": a.purpose,
                   "project_id": a.project, "state_root": a.state_root,
                   "tcmdb_root": a.tcmdb_root}
        envelope = call(a.tool, arguments, context)
        if a.text:
            sys.stdout.write(envelope["text"] + "\n")
        else:
            sys.stdout.write(json.dumps(envelope, ensure_ascii=False,
                                        indent=None if a.compact else 2) + "\n")
        return 0 if envelope["status"] in ("succeeded", "job_submitted") else 1
    parser.print_help()
    return 2
