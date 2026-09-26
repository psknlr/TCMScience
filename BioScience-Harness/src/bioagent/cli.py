"""bioagent command line: fetch datasets, list what is fetchable, verify sources, doctor.

    python -m bioagent.cli fetch <component_id|filename> [--confirm]
    python -m bioagent.cli fetchable
    python -m bioagent.cli sources
    python -m bioagent.cli doctor [--json] [--smoke]
    python -m bioagent.cli skills [--dir skills/tcm]
    python -m bioagent.cli skill <id> --arg name=value [--dir skills/tcm] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .acquisition import BulkDatasetProvider, Downloader, acquisition_for
from .config import data_lake_dir
from .runtime.registry import ComponentRegistry, Resolver


def _registry() -> ComponentRegistry:
    return ComponentRegistry(BulkDatasetProvider().discover())


def _skill_callables() -> dict:
    """The P0 skills, by id. Defined in :mod:`bioagent.governed`, which cross-checks
    each against its manifest on every run."""
    from .governed import skill_callables

    return skill_callables()


def _cmd_skills(a) -> int:
    """List what this installation can run, and what each skill refuses to do."""
    import json as _json

    from .skills.loader import load_skills

    loaded, refused = load_skills(a.dir)
    callables = _skill_callables()
    rows = []
    for skill in loaded:
        spec = skill.spec
        rows.append({
            "id": spec.id, "version": spec.version, "name": spec.name,
            "summary": spec.summary,
            "runnable": spec.id in callables,
            "claim_kinds": list(spec.evidence.claim_kinds),
            "forbidden_claims": list(spec.evidence.forbidden_claims),
            "max_evidence_tier": spec.evidence.max_tier,
            "network": list(spec.permissions.network),
            "sources": list(spec.sources),
            "content_hash": skill.content_hash,
        })
    for name, why in refused:
        rows.append({"id": name.name, "runnable": False, "error": why})

    if a.json:
        print(_json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print(f"no skills found under {a.dir}")
        print("  (run from the repository root, or pass --dir)")
        return 1
    print(f"{len(rows)} skill(s) under {a.dir}\n")
    for row in rows:
        if "error" in row:
            print(f"  ✗ {row['id']}: {row['error']}")
            continue
        mark = "✓" if row["runnable"] else "·"
        print(f"  {mark} {row['id']}@{row['version']}   [{row['content_hash'][:12]}]")
        print(f"      {row['summary'][:88]}")
        print(f"      claims: {', '.join(row['claim_kinds'])}"
              f"   max evidence: {row['max_evidence_tier']}")
    print("\nRun one with:  python -m bioagent.cli skill <id> --arg <name>=<value>")
    return 0


def _cmd_skill(a) -> int:
    """Run a single skill through the governed path and report what it produced.

    Everything goes through :func:`bioagent.governed.run_governed`: the manifest is
    read from ``--dir`` (a missing directory is refused, not ignored), its
    entrypoint and lock pin are checked, the run is recorded in a PSH audit chain,
    and the artifact is verified — receipts, outputs and attestation — before the
    verdict is printed. The exit code is 0 only when release is authorised.
    """
    import inspect
    import json as _json

    from .governed import GovernedRunRefused, coerce_arguments, run_governed

    callables = _skill_callables()
    fn = callables.get(a.skill_id)
    if fn is None:
        print(f"unknown skill {a.skill_id!r}", file=sys.stderr)
        print(f"  available: {', '.join(sorted(callables))}", file=sys.stderr)
        return 2

    raw: dict[str, str] = {}
    for item in a.arg:
        if "=" not in item:
            print(f"--arg {item!r} must be name=value", file=sys.stderr)
            return 2
        name, _, value = item.partition("=")
        raw[name.strip()] = value

    signature = inspect.signature(fn)
    first = next(iter(signature.parameters))
    if first not in raw:
        # Each skill names its subject differently (`names`, `subject`,
        # `formula_name`). Rather than guess, report the expected shape.
        print(f"skill {a.skill_id!r} needs --arg {first}=<value>", file=sys.stderr)
        print(f"  parameters: {', '.join(signature.parameters)}", file=sys.stderr)
        return 2

    try:
        kwargs = coerce_arguments(fn, raw)
        run = run_governed(a.skill_id, kwargs, skill_dir=a.dir,
                           state_dir=a.state_dir or None,
                           output_dir=a.out_dir or None,
                           lockfile=a.lockfile or None)
    except GovernedRunRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                                  # noqa: BLE001
        print(f"skill failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    artifact, verdict = run.artifact, run.verdict
    code = 0 if verdict.release_authorized else 1
    if a.json or a.out:
        payload = artifact.document()
        payload["validation"] = verdict.as_dict()
        payload["governed"] = {"audit_head": run.audit_head, "state_dir": run.state_dir,
                               "output_dir": run.output_dir,
                               "skill_content_hash": run.content_hash}
        text = _json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
            print(f"wrote {a.out}")
        else:
            print(text)
        return code

    print(f"{artifact.skill_id}@{artifact.skill_version}   [{run.content_hash[:12]}]")
    print(f"  question:   {artifact.question}")
    print(f"  versions:   {artifact.composite_version_string}")
    print(f"  sources:    {len(artifact.sources)}"
          f"   evidence: {len(artifact.evidence)}"
          f"   claims: {len(artifact.claims)}"
          f"   outputs: {len(artifact.outputs)} -> {run.output_dir}")
    print(f"  validation: {'publishable' if verdict.publishable else 'REFUSED'}")
    for state, ok in verdict.states.items():
        print(f"      {'✓' if ok else '✗'} {state}")
    print(f"  audit head: {run.audit_head[:16]}…  ({run.state_dir})")
    if not verdict.publishable:
        for violation in verdict.violations:
            print(f"      {violation}")
    for note in verdict.unverified:
        print(f"      unverified: {note}")
    for claim in artifact.claims:
        print(f"  claim [{claim.claim_kind}] {claim.text[:80]}")
    print(f"  limitations ({len(artifact.limitations)}):")
    for limit in artifact.limitations[:4]:
        print(f"      - {limit[:88]}")
    if len(artifact.limitations) > 4:
        print(f"      (+{len(artifact.limitations) - 4} more)")
    return code


def _cmd_scout(a) -> int:
    """Discover candidates and write a report.

    Deliberately cannot promote. There is no flag for it: the only writer of the
    stable registry is `Registry.promote`, which requires a `PromotionDecision`
    naming a human, and a scheduled CLI invocation is not one.
    """
    from .updates import Registry, Scout, load_sources, rank, score_candidate, to_candidate
    from .updates.scout import SourceError

    sources = []
    path = Path(a.sources)
    if path.is_file():
        try:
            sources = load_sources(path.read_text(encoding="utf-8"))
        except SourceError as exc:
            print(f"source registry refused: {exc}", file=sys.stderr)
            return 2
    elif a.skills:
        # A local scan needs no source registry; that is the CI path.
        pass
    else:
        print(f"no source registry at {path} and no --skills tree given", file=sys.stderr)
        return 2

    roots = [Path(s) for s in (a.skills or [])]
    report = Scout(sources, offline=True, local_roots=roots).run()
    scout = Scout(sources, offline=True, local_roots=roots)

    candidates = []
    for found in report.found:
        audit = scout.audit(found)
        score = score_candidate(found, report=audit)
        candidates.append(to_candidate(found, score, report=audit))

    ordered = rank(candidates)
    if a.eligible_only:
        ordered = tuple(c for c in ordered if c.eligible)

    registry = Registry()                       # read-only here; nothing is promoted
    payload = {
        "report": {"found": len(report.found),
                   "needs_adapter": len(report.needs_adapter),
                   "unreachable": len(report.unreachable)},
        "unreachable": [{"source": s, "reason": r} for s, r in report.unreachable],
        "needs_adapter": list(report.needs_adapter),
        "candidates": [c.as_dict() for c in ordered],
        "stable": [v.as_dict() for v in registry.stable_versions()],
        "note": ("Candidates only. Promotion requires a reviewed PromotionDecision and "
                 "is not performed by this command."),
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    print(report.explain())
    for c in ordered:
        flag = "eligible" if c.eligible else "ELIMINATED"
        print(f"  {flag:<10} {c.spec.composite_id:<52} {c.score:5.1f}")
    return 0


def _cmd_registry_release(a) -> int:
    """Cut a release bundle from a lockfile. Refuses an unprovenanced registry."""
    from datetime import datetime, timezone

    from .updates import RegistryRelease, load_lockfile

    lockfile = Path(a.lockfile)
    if not lockfile.is_file():
        print(f"no lockfile at {lockfile}", file=sys.stderr)
        return 2
    try:
        versions = load_lockfile(lockfile.read_text(encoding="utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        print(f"lockfile refused: {exc}", file=sys.stderr)
        return 2

    missing = [v.skill_id for v in versions if not v.approved_by]
    if missing:
        print(f"refusing to release: {missing} carry no approval attribution",
              file=sys.stderr)
        return 1

    created = a.created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if a.season:
        versions = tuple(
            type(v)(**{**{f: getattr(v, f) for f in v.__dataclass_fields__},
                       "benchmark_version": a.season}) for v in versions)
    release = RegistryRelease(release_id=a.release_id, created_at=created,
                              entries=versions)
    release = type(release)(release_id=release.release_id,
                            created_at=release.created_at, entries=release.entries,
                            digest=release.compute_digest(),
                            notes=f"cut against {a.season or 'unversioned'}")
    text = json.dumps(release.as_dict(), indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0 if release.verify() else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bioagent")
    ap.add_argument("--dest", default=None, help="data directory (default: data lake dir)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download a fetchable dataset")
    f.add_argument("target", help="component id or filename")
    f.add_argument("--confirm", action="store_true", help="allow downloads above the size gate")
    sub.add_parser("fetchable", help="list datasets that can be fetched")
    sub.add_parser("sources", help="list public API connectors")
    d = sub.add_parser("doctor", help="report what this installation can do, and what it cannot")
    d.add_argument("--json", action="store_true", help="machine-readable report")
    d.add_argument("--smoke", action="store_true", help="also run every native tool's example")

    # -- governed skill updates ------------------------------------------
    sc = sub.add_parser("scout", help="discover and score candidate skills (never promotes)")
    sc.add_argument("--sources", default="registry/skill_sources.yaml",
                    help="declared source registry")
    sc.add_argument("--skills", action="append", default=[],
                    help="a directory tree to scan; repeatable")
    sc.add_argument("--out", default="", help="write the candidate report here")
    sc.add_argument("--eligible-only", action="store_true",
                    help="omit candidates eliminated by a hard condition")

    rr = sub.add_parser("registry-release",
                        help="cut a verifiable release from the stable lockfile")
    rr.add_argument("--lockfile", default="registry/skills.lock.yaml")
    rr.add_argument("--release-id", required=True)
    rr.add_argument("--season", default="")
    rr.add_argument("--created-at", default="")
    rr.add_argument("--out", default="")

    # -- running the skills -------------------------------------------------
    sk = sub.add_parser("skills", help="list the skills this installation can run")
    sk.add_argument("--dir", default="skills/tcm",
                    help="where the skill directories live")
    sk.add_argument("--json", action="store_true", help="machine-readable output")

    sr = sub.add_parser("skill", help="run one skill and show what it produced")
    sr.add_argument("skill_id", help="skill id, e.g. normalize-tcm-entities")
    sr.add_argument("--dir", default="skills/tcm",
                    help="where the skill manifests live; the skill must be here")
    sr.add_argument("--lockfile", default="",
                    help="pin to check against (default: registry/skills.lock.yaml "
                         "found above --dir)")
    sr.add_argument("--state-dir", default="",
                    help="PSH state directory holding the audit chain (default: temporary)")
    sr.add_argument("--out-dir", default="",
                    help="where to write the declared outputs (default: temporary)")
    sr.add_argument("--arg", action="append", default=[],
                    help="a skill argument, as name=value; repeatable")
    sr.add_argument("--out", default="", help="write the artifact JSON here")
    sr.add_argument("--json", action="store_true", help="machine-readable output")

    a = ap.parse_args(argv)

    if a.cmd == "scout":
        return _cmd_scout(a)
    if a.cmd == "registry-release":
        return _cmd_registry_release(a)
    if a.cmd == "skills":
        return _cmd_skills(a)
    if a.cmd == "skill":
        return _cmd_skill(a)

    dest = Path(a.dest) if getattr(a, "dest", None) else data_lake_dir()
    if a.cmd == "doctor":
        from .doctor import diagnose, render

        report = diagnose(data_lake=dest, smoke=a.smoke)
        print(json.dumps(report, indent=1, ensure_ascii=False, default=str) if a.json
              else render(report))
        return 0 if report["verdict"] != "blocked" else 1
    reg = _registry()
    if a.cmd == "fetchable":
        present = {p.name for p in dest.iterdir()} if dest.is_dir() else set()
        res = Resolver(reg, dataset_probe=lambda n: n in present)
        for m in reg:
            r = res.resolve(m.id)
            state = "present" if not r.missing_datasets else ("fetchable" if r.fetchable else "blocked")
            spec = acquisition_for(m)
            print(f"{state:<9} {m.id:<48} {spec.host if spec else ''}")
        return 0
    if a.cmd == "sources":
        from .providers.public_apis import SOURCES
        for s in SOURCES:
            print(f"{s.key:<15} {s.host:<36} {len(s.operations)} ops  {s.license}")
        return 0
    # fetch
    m = reg.get(a.target) or next((x for x in reg if x.name == a.target), None)
    if m is None:
        print(f"unknown dataset {a.target!r}; try `fetchable`", file=sys.stderr)
        return 2
    spec = acquisition_for(m)
    dl = Downloader(dest, log=lambda s: print(s, file=sys.stderr))
    try:
        out = dl.fetch(spec.url, spec.filename, checksum=spec.checksum,
                       expected_bytes=spec.expected_bytes, confirm=a.confirm)
    except Exception as exc:  # noqa: BLE001
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"path": str(out.path), "bytes": out.bytes, "verified": out.verified,
                      "checksum": out.checksum, "from_cache": out.from_cache}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
