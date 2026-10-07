"""``python -m bioagent.mcp``: review a server before admitting it, and check a registry.

    python -m bioagent.mcp review DRAFT.yaml    # the reviewed entry, to read and commit
    python -m bioagent.mcp check [REGISTRY]     # every server's digest, and every refusal

``check`` exits non-zero when any entry is refused, so it can guard a change to the
registry the way the lockfile check guards the skills.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import MCPCallError, MCPConfigError, load_registry, render_entry, review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bioagent.mcp",
                                     description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    drafted = sub.add_parser("review", help="pin what a drafted server offers now")
    drafted.add_argument("draft", help="YAML: one entry whose tools lists the names to allow")
    checked = sub.add_parser("check", help="validate a registry file")
    checked.add_argument("registry", nargs="?", help="default: registry/mcp_servers.yaml")
    args = parser.parse_args(argv)

    if args.cmd == "review":
        import yaml

        try:
            entry, listing = review(yaml.safe_load(Path(args.draft).read_text("utf-8")))
        except (MCPConfigError, MCPCallError, OSError, yaml.YAMLError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        sys.stdout.write(render_entry(entry, listing))
        return 0

    try:
        registry = load_registry(args.registry)
    except MCPConfigError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    for sid, config in registry.servers.items():
        print(f"{sid}\t{config.transport}\t{config.destination}\t{config.digest}")
    for sid, why in registry.refused:
        print(f"REFUSED {sid}: {why}", file=sys.stderr)
    print(f"{len(registry)} server(s) admitted from {registry.source}; "
          f"registry digest {registry.digest}")
    return 1 if registry.refused else 0


if __name__ == "__main__":
    sys.exit(main())
