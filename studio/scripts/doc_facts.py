#!/usr/bin/env python3
"""Every number the docs restate about the tool catalog, checked against the catalog itself.

    python3 studio/scripts/doc_facts.py            # prints the facts, computed from the code
    python3 studio/scripts/doc_facts.py --check    # fails when a doc restates a number the catalog does not give

The facts come from ``tcmstudio.catalog.build_catalog("browser", probe=False)`` (what the site ships, built the
way the site build builds it) and from ``compat_matrix.rows()`` for the browser classes: entries in all and by kind,
core tools, connector sources and operations, native tools and their domains, governed and pinned skills, entries
that run in the browser and those only the runner runs, and the class counts.

Two kinds of check run over README.md, README.zh-CN.md, studio/README.md, studio/docs/*.md,
BioScience-Harness/README.md and docs/*.md:

* anchors: a phrase a given doc is known to use (``| 3 | … | 3 |`` in the class table, ``642 entries: native 150,
  …``); each must still be found, so a reworded sentence is noticed instead of silently escaping the check;
* phrases anywhere ("N public sources", "N typed operations", "N native tools", "N 个原生工具", "N entries", …):
  every number in one must equal the fact it names.

A dated report or a release note states the counts of its own day and is left as written; each such file or
section is listed below with the reason. Nothing else is exempt.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent

for src in (STUDIO / "runner" / "src", REPO / "BioScience-Harness" / "src", REPO / "PSH-Harness" / "src",
            Path(__file__).resolve().parent):
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

#: the docs whose numbers are checked (globs from the repository root)
DOCS = ("README.md", "README.zh-CN.md", "studio/README.md", "studio/docs/*.md", "BioScience-Harness/README.md",
        "docs/*.md")

#: whole files that state the counts of their own date, with the reason
HISTORICAL_FILES = {
    "docs/release-qualification-2026-10.md": "a dated release-qualification report: it records the catalog of "
                                             "the October 2026 release it qualified",
    "docs/audit-2026-09-response.md": "a dated audit response (September 2026)",
    "docs/audit-2026-10-response.md": "a dated audit response (October 2026)",
}
#: sections (from a heading matching the pattern to the next heading of the same or a higher level) that state
#: the counts of a past version, with the reason
HISTORICAL_SECTIONS = {
    "BioScience-Harness/README.md": (re.compile(r"^(##) v\d"), "release notes: each '## vX' section states the "
                                                               "counts of that version"),
}

_N = r"(\d[\d,]*)"
#: (pattern, fact): wherever the phrase appears, its number must be the fact
PHRASES: tuple[tuple[str, str], ...] = (
    (rf"(?<![\d.,~]){_N} (?:catalog )?entries\b", "entries"),
    (rf"(?<![\d.,~]){_N} (?:verified )?public (?:sources|connectors)\b", "sources"),
    (rf"(?<![\d.,~]){_N} typed operations\b", "operations"),
    (rf"(?<![\d.,~]){_N} native (?:[A-Za-z]+,? )*?tools\b", "native"),
    (rf"(?<![\d.,~]){_N} (?:pinned governed|pinned) skills?\b", "pinned_skills"),
    (rf"\*\*{_N} run in the browser\*\*", "browser"),
    (rf"\({_N} core tools\)", "core"),
    (rf"{_N} 个原生工具", "native"),
    (rf"{_N} 个(?:已验证的)?公共数据源", "sources"),
    (rf"{_N} 个带类型的操作", "operations"),
    (rf"{_N} 个(?:目录)?条目", "entries"),
    (rf"{_N} 个固定版本的受治理 Skill", "pinned_skills"),
)
#: (doc, pattern, facts): a phrase this doc is known to use; it must still be there. A fact None is a label group;
#: "class.{}" takes the label before it.
ANCHORS: tuple[tuple[str, str, tuple[str | None, ...]], ...] = (
    ("README.md", rf"{_N} public sources, {_N} typed operations and {_N} native tools",
     ("sources", "operations", "native")),
    ("README.md", rf"{_N} verified public sources with {_N} typed operations, {_N} native tools in {_N} domains",
     ("sources", "operations", "native", "native_domains")),
    ("README.zh-CN.md", rf"{_N} 个公共数据源、{_N} 个带类型的操作、{_N} 个原生工具", ("sources", "operations", "native")),
    ("README.zh-CN.md", rf"{_N} 个已验证的公共数据源（{_N} 个带类型的操作）、{_N} 个领域的 {_N} 个原生工具",
     ("sources", "operations", "native_domains", "native")),
    ("studio/README.md", rf"{_N} 个原生工具、{_N} 个固定版本的受治理 Skill、临床辨证都能在浏览器里", ("native", "pinned_skills")),
    ("studio/README.md", rf"{_N} 个原生工具、{_N} 个固定版本的受治理 Skill 和临床辨证都可以运行", ("native", "pinned_skills")),
    ("studio/docs/ARCHITECTURE.md", rf"\({_N} native tools, the governed skills, the clinic, the TCM\s+data hub, {_N} "
                                    r"public connectors", ("native", "sources")),
    ("studio/docs/ARCHITECTURE.md", rf"{_N} native tools, the {_N} pinned governed skills", ("native", "pinned_skills")),
    ("studio/docs/ARCHITECTURE.md", rf"offered {_N} core tools", ("core",)),
    ("studio/docs/ARCHITECTURE.md", rf"reach every other entry \({_N}\)", ("entries",)),
    ("studio/docs/BROWSER_COMPUTE.md", rf"has \*\*{_N} entries\*\* \({_N} core tools\); \*\*{_N} run in the browser\*\*",
     ("entries", "core", "browser")),
    ("studio/docs/BROWSER_COMPUTE.md", r"^\| (1g|[1-8]) \| [^|\n]+\| (\d+) \|$", (None, "class.{}")),
    ("studio/docs/BROWSER_COMPUTE.md", rf"Exact CPU kernels for {_N} tools", ("kernel_tools",)),
    ("studio/docs/BROWSER_COMPUTE.md", rf"compatibility matrix of all {_N} catalog entries", ("entries",)),
    ("studio/docs/CONTRACT_NOTES.md",
     rf"{_N} entries: native {_N}, skill {_N}, connector {_N}, clinic {_N}\s+\([^)]*\), tcmdb {_N}, study {_N}, "
     rf"job {_N}, system {_N}\.",
     ("entries", "kind.native", "kind.skill", "kind.connector", "kind.clinic", "kind.tcmdb", "kind.study", "kind.job",
      "kind.system")),
    ("BioScience-Harness/README.md", rf"and {_N} public sources", ("sources",)),
    ("BioScience-Harness/README.md", rf"tools/\s+{_N} native bioinformatics, clinical and TCM tools", ("native",)),
)


def facts() -> dict[str, int]:
    """The numbers the docs may restate, computed from the code."""
    from tcmstudio.catalog import build_catalog
    import compat_matrix

    doc = build_catalog("browser", probe=False)
    items = doc["entries"]
    connectors = [e for e in items if e["kind"] == "connector"]
    classes = Counter(r["class"] for r in compat_matrix.rows())
    out = {
        "entries": len(items), "core": len(doc["core"]),
        **{f"kind.{k}": n for k, n in doc["counts"]["by_kind"].items()},
        "sources": len({e["connector"]["key"] for e in connectors}), "operations": len(connectors),
        "native": doc["counts"]["by_kind"]["native"],
        "native_domains": len({e.get("domain") for e in items if e["kind"] == "native"}),
        "skills": doc["counts"]["by_kind"]["skill"],
        "pinned_skills": sum(1 for e in items if e["kind"] == "skill" and (e.get("skill") or {}).get("pinned")),
        "browser": sum(1 for e in items if "browser" in (e.get("exec") or ())),
        "runner_only": sum(1 for e in items if "browser" not in (e.get("exec") or ())),
        "kernel_tools": sum(1 for e in items if (e.get("execution") or {}).get("browser_compute")),
        "live_verified": sum(1 for e in connectors
                             if ((e.get("verification") or {}).get("levels") or {}).get("live_upstream", {})
                             .get("status") == "PASS"),
        **{f"class.{c}": classes.get(c, 0) for c in compat_matrix.CLASS_LABELS},
    }
    return out


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 90 else text[:89] + "…"


def _line(text: str, at: int) -> int:
    return text.count("\n", 0, at) + 1


def _historical_spans(rel: str, text: str) -> list[tuple[int, int]]:
    """The character spans of the sections of this doc that are exempt."""
    if rel not in HISTORICAL_SECTIONS:
        return []
    pattern, _ = HISTORICAL_SECTIONS[rel]
    spans, start, level, offset = [], None, 0, 0
    for line in text.splitlines(keepends=True):
        heading = re.match(r"(#+) ", line)
        if heading and start is not None and len(heading.group(1)) <= level:
            spans.append((start, offset))
            start = None
        found = pattern.match(line)
        if found and start is None:
            start, level = offset, len(found.group(1))
        offset += len(line)
    if start is not None:
        spans.append((start, offset))
    return spans


def check_text(rel: str, text: str, known: dict[str, int], *, anchors: bool = True) -> list[str]:
    """The problems in one doc: a phrase whose number is not the fact it names, or an anchor no longer found."""
    if rel in HISTORICAL_FILES:
        return []
    spans = _historical_spans(rel, text)

    def exempt(at: int) -> bool:
        return any(a <= at < b for a, b in spans)

    problems = []
    for pattern, fact in PHRASES:
        for m in re.finditer(pattern, text):
            if exempt(m.start()):
                continue
            got, want = _number(m.group(1)), known[fact]
            if got != want:
                problems.append(f"{rel}:{_line(text, m.start())}: '{_short(m.group(0))}' says {got}; the catalog "
                                f"gives {fact} = {want}")
    for doc, pattern, keys in (ANCHORS if anchors else ()):
        if doc != rel:
            continue
        found = list(re.finditer(pattern, text, re.M))
        if not found:
            problems.append(f"{rel}: the phrase /{pattern}/ is no longer there; reword the anchor in "
                            "studio/scripts/doc_facts.py to the doc's new wording (and keep its numbers checked)")
        for m in found:
            label = None
            for i, key in enumerate(keys, start=1):
                if key is None:
                    label = m.group(i)
                    continue
                fact = key.format(label) if "{}" in key else key
                if fact not in known:
                    problems.append(f"{rel}:{_line(text, m.start())}: no fact {fact!r} for '{m.group(0)}'")
                    continue
                got, want = _number(m.group(i)), known[fact]
                if got != want:
                    problems.append(f"{rel}:{_line(text, m.start(i))}: '{_short(m.group(0))}' says {got}; "
                                    f"the catalog gives {fact} = {want}")
    return problems


def docs(root: Path = REPO) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in DOCS:
        for p in sorted(root.glob(pattern)):
            if p.is_file():
                seen.setdefault(p, None)
    return list(seen)


def check(root: Path = REPO, known: dict[str, int] | None = None) -> list[str]:
    known = known or facts()
    problems = []
    for path in docs(root):
        rel = path.relative_to(root).as_posix()
        problems += check_text(rel, path.read_text(encoding="utf-8"), known)
    missing = sorted({doc for doc, _, _ in ANCHORS} - {p.relative_to(root).as_posix() for p in docs(root)})
    problems += [f"{doc}: anchored doc not found" for doc in missing]
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail when a doc restates a number the catalog does not give")
    args = ap.parse_args()
    known = facts()
    if not args.check:
        print(json.dumps(known, indent=2, sort_keys=True))
        return 0
    problems = check(REPO, known)
    for p in problems:
        print(p)
    if problems:
        print(f"{len(problems)} stale number(s); fix the docs (or, for a dated report, list it in HISTORICAL_FILES "
              "with the reason)")
        return 1
    print(f"doc facts: every restated catalog number agrees ({len(docs(REPO))} docs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
