#!/usr/bin/env python3
"""The lockfile and the tree must agree, or the pin is fiction.

Every entry in `skills.lock.yaml` names a content hash. If the skill on disk
hashes differently, the lockfile is describing something that is not there —
which is worse than having no lockfile, because it reads as a guarantee.
"""
from __future__ import annotations

import _bootstrap

_bootstrap.bootstrap()

import sys
from pathlib import Path

ROOT = _bootstrap.ROOT

def main() -> int:
    from bioagent.skills.loader import load_skills
    from bioagent.updates import load_lockfile

    lockfile = ROOT / "registry" / "skills.lock.yaml"
    skills_dir = ROOT / "skills" / "tcm"
    if not lockfile.is_file():
        print(f"no lockfile at {lockfile}", file=sys.stderr)
        return 1

    versions = load_lockfile(lockfile.read_text(encoding="utf-8"))
    loaded, refused = load_skills(skills_dir)
    if refused:
        print("skills refused to load:", file=sys.stderr)
        for directory, why in refused:
            print(f"  {directory.name}: {why}", file=sys.stderr)
        return 1

    by_id = {s.spec.id: s for s in loaded}
    problems: list[str] = []
    for version in versions:
        skill = by_id.get(version.skill_id)
        if skill is None:
            problems.append(f"{version.skill_id} is locked but not present in the tree")
        elif skill.content_hash != version.content_hash:
            problems.append(
                f"{version.skill_id}: locked {version.content_hash[:12]} but the tree "
                f"hashes {skill.content_hash[:12]}")
        if not version.approved_by:
            problems.append(f"{version.skill_id} is locked with no approver")
        if not version.license_spdx:
            problems.append(f"{version.skill_id} is locked with no licence")

    unlocked = sorted(set(by_id) - {v.skill_id for v in versions})
    if unlocked:
        problems.append(f"present but not locked: {unlocked}")

    # Candidate skills are not locked: that is what makes them candidates. They must
    # still load and compile-check, and none may share an id with a stable skill.
    candidates = []
    root = ROOT / "skills" / "candidates"
    for group in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        found, failed = load_skills(group)
        for directory, why in failed:
            problems.append(f"candidate {group.name}/{directory.name} refused to load: {why}")
        candidates += [s.spec.id for s in found]
    clash = sorted(set(candidates) & set(by_id))
    if clash:
        problems.append(f"candidates share an id with a stable skill: {clash}")
    locked_candidates = sorted(set(candidates) & {v.skill_id for v in versions})
    if locked_candidates:
        problems.append(f"locked skills that live under candidates/: {locked_candidates}")

    if problems:
        print("LOCKFILE CHECK FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"ok: {len(versions)} skill(s) pinned and matched; {len(candidates)} candidate(s) "
          "load, unlocked until promoted")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
