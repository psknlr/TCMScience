"""bioagent command line: fetch datasets, list what is fetchable, verify sources, doctor.

    python -m bioagent.cli fetch <component_id|filename> [--confirm]
    python -m bioagent.cli fetchable
    python -m bioagent.cli sources
    python -m bioagent.cli doctor [--json] [--smoke]
    python -m bioagent.cli skills [--dir skills/tcm]
    python -m bioagent.cli skill <id> --arg name=value [--dir skills/tcm] [--json]
    python -m bioagent.cli research "<question>" --snapshots DIR --ledger FILE \
        --state-dir DIR --out DIR [--activity npass ...] [--background assayed]
    python -m bioagent.cli tcmdb sources [--module M2] [--access live_api]
    python -m bioagent.cli tcmdb fetch itcm [--all] && python -m bioagent.cli tcmdb build itcm
    python -m bioagent.cli tcmdb relations herb_ingredient --subject 黄芪
    python -m bioagent.cli tcmdb live dcabm_tcm herb_blood names='["SANG YE"]'
    python -m bioagent.cli rnaseq --samples samples.csv --transcripts tx.fa \
        --annotation genes.gtf --design "~ condition" --out results/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .acquisition import BulkDatasetProvider, acquisition_for
from .config import data_lake_dir, registry_dir, skills_dir
from .runtime.registry import ComponentRegistry, Resolver


def _registry() -> ComponentRegistry:
    return ComponentRegistry(BulkDatasetProvider().discover())


def _skill_callables() -> dict:
    """The P0 skills, by id. Defined in :mod:`bioagent.governed`, which cross-checks
    each against its manifest on every run."""
    from .governed import skill_callables

    return skill_callables()


def _skill_root(a) -> Path:
    """``--dir``, else the reviewed TCM skills this installation ships.

    The default was the relative path ``skills/tcm``: right only when the command was run
    from the repository's package directory, and never right for an installed wheel.
    """
    return Path(a.dir) if a.dir else skills_dir() / "tcm"


def _cmd_skills(a) -> int:
    """List what this installation can run, and what each skill refuses to do."""
    import json as _json

    from .skills.loader import load_skills

    a.dir = _skill_root(a)
    if not Path(a.dir).is_dir():
        # load_skills reports a missing root as one refused row; that is no skill at all
        print(f"no skills found under {a.dir}: it is not a directory\n"
              "  (pass --dir to name the directory that holds the skills; a relative path "
              "is read from the current directory)", file=sys.stderr)
        return 1
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
        print("  (pass --dir to name the directory that holds the skills)")
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
        run = run_governed(a.skill_id, kwargs, skill_dir=_skill_root(a),
                           state_dir=a.state_dir or None,
                           output_dir=a.out_dir or None,
                           lockfile=a.lockfile or None,
                           allow_unpinned=a.allow_unpinned)
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
                               "skill_content_hash": run.content_hash,
                               "lockfile": run.lockfile}
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
    print(f"  pinned by:  "
          f"{run.lockfile or 'no lockfile (the skill was not checked against a pin)'}")
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


def _pairs(items, *, parse_json: bool = False) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"expected name=value, got {item!r}")
        key, value = item.split("=", 1)
        if parse_json:
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass                                   # a plain string
        out[key.strip()] = value
    return out


def _cmd_tcmdb(a) -> int:
    """The TCM data hub (``bioagent.tcmdb``): catalogue, downloads, local queries, live calls."""
    from .tcmdb import DATASETS, HubError, TCMDataHub

    hub = TCMDataHub(a.root or None)
    show = lambda obj: print(json.dumps(obj, indent=1, ensure_ascii=False, default=str))  # noqa: E731
    try:
        if a.tcmdb_cmd == "sources":
            cards = hub.catalog(module=a.module, access=a.access, name=a.name)
            if a.json:
                show([c.as_dict() for c in cards])
            else:
                for c in cards:
                    via = c.connector or c.dataset or "-"
                    print(f"{c.no:>2} {c.access:18} {via:20} {c.name}  [{','.join(c.modules)}]")
                print(f"{len(cards)} sources")
            return 0
        if a.tcmdb_cmd == "status":
            show(hub.status(a.dataset))
            return 0
        if a.tcmdb_cmd == "fetch":
            # the verified acquisition (acquisition.service): a resume only onto the same
            # file, a lock per file, archives read through before a file is kept; SIGTERM (a
            # Studio job's cancel) stops it at the next block and keeps the partial file
            from .acquisition.service import acquire_dataset, cancel_on_signals
            doc = acquire_dataset(hub, a.dataset, include_optional=a.all, confirm=a.confirm,
                                  mirrors=a.mirror, cancel=cancel_on_signals(),
                                  report_path=a.report or None,
                                  log=lambda m: print(m, file=sys.stderr, flush=True))
            print(json.dumps({k: doc[k] for k in ("dataset", "status", "kept", "bytes",
                                                  "verified", "cancelled")},
                             ensure_ascii=False))
            if doc["cancelled"]:
                print("cancelled: the partial file is kept and resumes, verified, on the next "
                      "fetch", file=sys.stderr)
                return 130
            if not doc["files"]:
                # nothing was asked for (every file of the dataset is optional): not a success
                print(f"nothing fetched: every file of {a.dataset} is optional; pass --all to "
                      "fetch them", file=sys.stderr)
                return 1
            if all(r["ok"] for r in doc["files"]):
                return 0
            bad = next(r for r in doc["files"] if not r["ok"])
            # the last line of stderr is what a job's error shows
            print(f"{bad['status']}: {bad['file']}: {bad.get('reason') or bad.get('error')}",
                  file=sys.stderr)
            return 1
        if a.tcmdb_cmd == "import-raw":
            show([hub.import_raw(a.dataset, path, name=a.name) for path in a.files])
            return 0
        if a.tcmdb_cmd == "build":
            keys = [d.key for d in DATASETS if hub.raw_dir(d.key).exists()] \
                if a.dataset == "all" else [a.dataset]
            for key in keys:
                report = hub.build(key, log=lambda m: None)
                print(json.dumps({"dataset": key, "tables": report["tables"],
                                  "relations": report["relations"],
                                  "unresolved": report["unresolved"],
                                  "relations_digest": report["relations_digest"],
                                  "missing": report["missing"]}, ensure_ascii=False))
            return 0
        if a.tcmdb_cmd == "check":
            keys = hub.built() if a.dataset == "all" else [a.dataset]
            if not keys:
                # nothing was checked, so it is not a pass either
                print(f"no built dataset to check under {hub.root}: build one first "
                      "(tcmdb build <dataset>)", file=sys.stderr)
                return 1
            results = [hub.check(k, rebuild=not a.no_rebuild) for k in keys]
            show(results if len(results) > 1 else results[0])
            return 0 if all(r["ok"] for r in results) else 1
        if a.tcmdb_cmd == "tables":
            show(hub.tables(a.dataset))
            return 0
        if a.tcmdb_cmd == "query":
            show(hub.query(a.dataset, a.table, where=_pairs(a.where),
                           contains=_pairs(a.contains), limit=a.limit))
            return 0
        if a.tcmdb_cmd == "relations":
            show(hub.relations(a.kind, subject=a.subject or None, object=a.object or None,
                               sources=a.source or None, evidence=a.evidence or None,
                               contains=a.contains, outcomes=a.outcome or None,
                               commercial=a.commercial, limit=a.limit))
            return 0
        if a.tcmdb_cmd == "consensus":
            result = hub.consensus(a.kind, subject=a.subject or None, object=a.object or None,
                                   merge_processed=a.merge_processed,
                                   min_support=a.min_support or None)
            result["items"] = result["items"][:a.limit]
            show(result)
            return 0
        if a.tcmdb_cmd == "compare":
            show(hub.compare(a.kind, a.subject, merge_processed=a.merge_processed))
            return 0
        if a.tcmdb_cmd == "survey":
            show(hub.survey(a.kind, sample=a.sample, min_sources=a.min_sources, seed=a.seed))
            return 0
        if a.tcmdb_cmd == "enrich":
            if a.symmap or a.herb:
                if a.symmap:
                    hub.enrich_symmap(a.symmap)
                if a.herb:
                    hub.enrich_herb(a.herb, label=a.label)
            else:
                show(hub.enrich(a.name)["ids"])
            return 0
        if a.tcmdb_cmd == "live":
            result = hub.live(a.connector, a.operation, **_pairs(a.args, parse_json=True))
            show({"status": result.status.value, "error": result.error, "value": result.value})
            return 0 if result.status.value in ("SUCCEEDED", "DEGRADED") else 1
        if a.tcmdb_cmd == "template":
            from .tcmdb.manual import templates
            for path in templates(a.dataset, a.out, demo=a.demo):
                print(path)
            return 0
        if a.tcmdb_cmd == "verify":
            from .tcmdb.manual import EXIT_NONE_CHECKED, verify
            report = verify(hub, a.dataset or None)
            if report["unknown"]:
                print(f"tcmdb: not manual datasets: {report['unknown']}", file=sys.stderr)
                return 2
            if a.json:
                show(report)
            else:
                print(f"hub: {report['hub']}")
                for r in report["results"]:
                    print(f"== {r['dataset']}: {'PASS' if r['ok'] else 'FAIL'}")
                    if r.get("error"):
                        print(f"   {r['error']}")
                    if "tables" in r:
                        print(f"   tables {r['tables']} | relations {r['relations']} | "
                              f"unresolved {r['unresolved']}")
                    if r.get("lookup"):
                        lk = r["lookup"]
                        queue = (f", {lk['unreviewed_rows']} unreviewed row(s) in the queue"
                                 if lk.get("unreviewed_rows") else "")
                        print(f"   lookup {lk['tool']}({lk['query']!r}): {lk['status']}, "
                              f"{lk['records']} record(s){queue}")
                    for line in r.get("problems", []):
                        print(f"   PROBLEM: {line}")
                    for line in r.get("warnings", []):
                        print(f"   warning: {line}")
                print(f"{report['outcome']} ({report['passed']}/{report['checked']} "
                      "datasets checked passed)")
            if not report["checked"]:
                return EXIT_NONE_CHECKED
            return 0 if report["passed"] == report["checked"] else 1
    except (HubError, KeyError, FileExistsError) as exc:
        print(f"tcmdb: {exc}", file=sys.stderr)
        return 2
    return 2


def _cmd_research(a) -> int:
    """Run a question through the closed research loop (``bioagent.research``).

    Exit 0 when the artifact may be released, including when every hypothesis was
    refuted and the negative result is what is released; 1 when release is not
    authorised; 2 when the question or a stage was refused; 3 when the run finished but one
    of its sixteen workflow stages FAILed its own check (a target resting on no measured
    edge, q-values that do not recompute, an export whose digest differs on disk), or its
    run_manifest.json could not be read: whatever the release check said, such a run is not
    a result (the research.run job counts 0 and 1 as results, 3 as a failure).
    """
    import json as _json
    from dataclasses import replace

    from .analysis.network_pharmacology import Parameters
    from .research import (QuestionRefused, ResearchRefused, default_protocol,
                           parse_question, run_research)
    from .research.loop import research_summary

    try:
        question = parse_question(a.question, disease=a.disease, formula=a.formula)
        params = replace(Parameters(), background=a.background, hits=a.hits,
                         permutations=a.permutations)
        protocol = default_protocol(question, activity=tuple(a.activity or ["npass"]),
                                    optional=tuple(a.optional or ["string"]),
                                    parameters=params)
        run = run_research(question, protocol=protocol, snapshot_root=a.snapshots,
                           ledger_path=a.ledger, state_dir=a.state_dir, output_dir=a.out,
                           accept_review=a.accept_review, profile=a.profile)
    except (QuestionRefused, ResearchRefused, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    # the run, its sixteen workflow stages and how far the data reached (from its manifest)
    summary, failed = research_summary(run, question)
    print(_json.dumps(summary, indent=2, ensure_ascii=False))
    if failed:
        for line in failed:
            print(f"failed: {line}", file=sys.stderr)
        return 3
    return 0 if run.released else 1


def _cmd_snapshots(a) -> int:
    """The research snapshots in one recorded run (``bioagent.sources.pipeline``).

    Exit 0 when every stage passed or is only unverified (a file with no reviewed checksum
    yet), 1 when a stage failed or was blocked; the report says which and what to do."""
    from .config import data_lake_dir
    from .sources import pipeline

    lake = data_lake_dir(a.dest)
    raw = Path(a.raw) if a.raw else lake / "raw"
    if a.snap_cmd == "plan":
        print(json.dumps(pipeline.plan(raw, a.sources, network=not a.no_network),
                         indent=1, ensure_ascii=False))
        return 0
    if a.snap_cmd == "import":
        try:
            rows = pipeline.import_files(raw, a.files)
        except (OSError, ValueError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    out = Path(a.out) if a.out else lake / "snapshots"
    ledger = Path(a.ledger) if a.ledger else out / "ledger.jsonl"
    doc = pipeline.build_research_snapshots(
        raw, out, ledger, sources=a.sources, network=not a.no_network, formulas=a.formula,
        formula_table=a.formula_table or None, confirm=a.confirm, offline=a.offline,
        report_path=a.report or None, mirrors=a.mirror,
        log=lambda m: print(m, file=sys.stderr))
    print(json.dumps(pipeline.summary(doc), indent=1, ensure_ascii=False))
    if doc["status"] in ("PASS", "UNVERIFIED"):
        return 0
    # the last line of stderr is what a job's error shows: the reason and the next step
    print(f"{doc['status']}: {doc.get('next_step') or 'see the report'}", file=sys.stderr)
    return 1


def _cmd_scout(a) -> int:
    """Discover candidates and write a report.

    Deliberately cannot promote. There is no flag for it: the only writer of the
    stable registry is `Registry.promote`, which requires a `PromotionDecision`
    naming a human, and a scheduled CLI invocation is not one.
    """
    from .updates import Registry, Scout, load_sources, rank, score_candidate, to_candidate
    from .updates.scout import SourceError

    sources = []
    path = Path(a.sources) if a.sources else registry_dir() / "skill_sources.yaml"
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
        "notes": list(report.notes),
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

    lockfile = Path(a.lockfile) if a.lockfile else registry_dir() / "skills.lock.yaml"
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
    sc.add_argument("--sources", default="",
                    help="declared source registry (default: the shipped "
                         "registry/skill_sources.yaml)")
    sc.add_argument("--skills", action="append", default=[],
                    help="a directory tree to scan; repeatable")
    sc.add_argument("--out", default="", help="write the candidate report here")
    sc.add_argument("--eligible-only", action="store_true",
                    help="omit candidates eliminated by a hard condition")

    rr = sub.add_parser("registry-release",
                        help="cut a verifiable release from the stable lockfile")
    rr.add_argument("--lockfile", default="",
                    help="the lockfile to release (default: the shipped "
                         "registry/skills.lock.yaml)")
    rr.add_argument("--release-id", required=True)
    rr.add_argument("--season", default="")
    rr.add_argument("--created-at", default="")
    rr.add_argument("--out", default="")

    # -- running the skills -------------------------------------------------
    sk = sub.add_parser("skills", help="list the skills this installation can run")
    sk.add_argument("--dir", default="",
                    help="where the skill directories live (default: the reviewed TCM "
                         "skills this installation ships)")
    sk.add_argument("--json", action="store_true", help="machine-readable output")

    sr = sub.add_parser("skill", help="run one skill and show what it produced")
    sr.add_argument("skill_id", help="skill id, e.g. normalize-tcm-entities")
    sr.add_argument("--dir", default="",
                    help="where the skill manifests live; the skill must be here "
                         "(default: the reviewed TCM skills this installation ships)")
    sr.add_argument("--lockfile", default="",
                    help="pin to check against (default: registry/skills.lock.yaml "
                         "found above --dir); a skill no lockfile pins is refused")
    sr.add_argument("--allow-unpinned", action="store_true",
                    help="run a skill no lockfile pins, for developing it: the run is "
                         "recorded but never attested, so never released")
    sr.add_argument("--state-dir", default="",
                    help="PSH state directory holding the audit chain (default: temporary)")
    sr.add_argument("--out-dir", default="",
                    help="where to write the declared outputs (default: temporary)")
    sr.add_argument("--arg", action="append", default=[],
                    help="a skill argument, as name=value; repeatable")
    sr.add_argument("--out", default="", help="write the artifact JSON here")
    sr.add_argument("--json", action="store_true", help="machine-readable output")

    rs = sub.add_parser("research", help="run a question through the closed research loop")
    rs.add_argument("question", help="e.g. '葛根芩连汤的实测靶点是否集中在某条通路？'")
    rs.add_argument("--disease", default="", help="ontology id, e.g. MONDO_0005148")
    rs.add_argument("--formula", default="",
                    help="the formula to study (a name, a name with its source book, or an "
                         "id); a question that names another formula is refused")
    rs.add_argument("--snapshots", required=True, help="snapshot root directory")
    rs.add_argument("--ledger", required=True, help="the snapshot ledger (jsonl)")
    rs.add_argument("--state-dir", required=True,
                    help="checkpoints and the PSH audit chain; reuse it to resume")
    rs.add_argument("--out", required=True, help="where the outputs and artifact go")
    rs.add_argument("--activity", action="append", default=[],
                    help="required activity source; repeatable (default: npass)")
    rs.add_argument("--optional", action="append", default=[],
                    help="optional source; repeatable (default: string)")
    rs.add_argument("--background", default="assayed", choices=("assayed", "reactome"))
    rs.add_argument("--hits", default="potency", choices=("potency", "screening"))
    rs.add_argument("--permutations", type=int, default=1000)
    rs.add_argument("--accept-review", action="store_true",
                    help="load snapshots whose QC status is 'review'")
    rs.add_argument("--profile", default="trusted_local",
                    choices=("trusted_local", "restricted_research", "sensitive_data"),
                    help="PSH deployment profile the analysis executes under")

    va = sub.add_parser("validate", add_help=False,
                        help="validation reports: real data, dependencies, databases "
                             "(bioagent validate --help)")
    va.add_argument("rest", nargs=argparse.REMAINDER)
    sn = sub.add_parser("snapshots", help="build the research loop's source snapshots in one "
                                          "recorded run: acquire, verify, build, ledger")
    snsub = sn.add_subparsers(dest="snap_cmd", required=True)
    for name, text in (("plan", "the raw files the sources need, and which are here"),
                       ("build", "acquire, verify, build and record the snapshots"),
                       ("import", "add raw files a person obtained, held to the reviewed "
                                  "checksums")):
        x = snsub.add_parser(name, help=text)
        x.add_argument("--raw", default="", help="raw file directory (default: <data lake>/raw)")
        if name == "import":
            x.add_argument("files", nargs="+")
            continue
        x.add_argument("--sources", nargs="+", default=["npass", "cmaup", "lotus"],
                       choices=("npass", "cmaup", "lotus"))
        x.add_argument("--no-network", action="store_true",
                       help="leave out STRING and Reactome (no enrichment or network)")
        if name == "plan":
            continue
        x.add_argument("--out", default="", help="snapshot root (default: <data lake>/snapshots)")
        x.add_argument("--ledger", default="", help="the ledger (default: <out>/ledger.jsonl, "
                                                    "where the research job reads it)")
        x.add_argument("--formula", action="append", default=[],
                       help="add this formula to the herb layer: an id or a name with its "
                            "source book, e.g. 桂枝汤《伤寒论》 (repeatable)")
        x.add_argument("--formula-table", default="",
                       help="the formula table; alone, every fully resolved formula of it "
                            "joins the herb layer")
        x.add_argument("--confirm", action="store_true", help="allow files above the size gate")
        x.add_argument("--offline", action="store_true",
                       help="download nothing: build from the files already here")
        x.add_argument("--report", default="", help="write the full report (JSON) here")
        x.add_argument("--mirror", action="append", default=[],
                       help="a directory that may hold the same raw files (the data hub's "
                            "raw/npass, raw/cmaup, raw/lotus): a copy that hashes to its "
                            "reviewed checksum is adopted as a hard link (repeatable)")

    tc = sub.add_parser("tcmdb", help="the TCM data hub: 66 catalogued sources, local "
                                      "stores, cross-source relations, live connectors")
    tc.add_argument("--root", default="", help="hub directory (default: $BIOAGENT_TCMDB "
                                                "or <data lake>/tcmdb)")
    tsub = tc.add_subparsers(dest="tcmdb_cmd", required=True)
    t = tsub.add_parser("sources", help="the source catalogue")
    t.add_argument("--module", default=None, help="M1..M14")
    t.add_argument("--access", default=None, help="live_api, snapshot, manual_import, "
                                                  "restricted, unreachable")
    t.add_argument("--name", default=None)
    t.add_argument("--json", action="store_true")
    t = tsub.add_parser("status", help="what is downloaded and built")
    t.add_argument("dataset", nargs="?", default=None)
    t = tsub.add_parser("fetch", help="download a dataset's files, each verified before it "
                                      "is kept")
    t.add_argument("dataset")
    t.add_argument("--all", action="store_true", help="include optional (large) files")
    t.add_argument("--confirm", action="store_true", help="allow files above the size gate")
    t.add_argument("--mirror", action="append", default=[],
                   help="a directory that may hold the same files (the research snapshots' "
                        "raw directory): a copy there that hashes to its reviewed checksum is "
                        "adopted as a hard link instead of downloaded (repeatable)")
    t.add_argument("--report", default="", help="write the acquisition report (JSON) here")
    t = tsub.add_parser("import-raw", help="add files a person obtained, verified against the "
                                           "reviewed checksum or size as a download would be")
    t.add_argument("dataset")
    t.add_argument("files", nargs="+")
    t.add_argument("--name", default=None, help="the dataset file a single FILE is (default: "
                                                 "its own name)")
    t = tsub.add_parser("build", help="load downloaded files into the local store")
    t.add_argument("dataset", help="a dataset key, or 'all'")
    t = tsub.add_parser("check", help="acceptance checks: files, rebuild digest, row "
                                      "vocabulary, licences, unresolved rows")
    t.add_argument("dataset", help="a dataset key, or 'all' (every built dataset)")
    t.add_argument("--no-rebuild", action="store_true", help="skip the two rebuilds")
    t = tsub.add_parser("tables", help="tables and columns of a built dataset")
    t.add_argument("dataset")
    t = tsub.add_parser("query", help="rows of one source table")
    t.add_argument("dataset")
    t.add_argument("table")
    t.add_argument("--where", action="append", default=[], help="column=value (exact)")
    t.add_argument("--contains", action="append", default=[], help="column=text (substring)")
    t.add_argument("--limit", type=int, default=20)
    t = tsub.add_parser("relations", help="relations across every built dataset")
    t.add_argument("kind", nargs="?", default=None,
                   help="herb_ingredient, ingredient_target, formula_herb, target_disease, ...")
    t.add_argument("--subject", default="")
    t.add_argument("--object", default="")
    t.add_argument("--source", action="append", default=[])
    t.add_argument("--evidence", action="append", default=[])
    t.add_argument("--contains", action="store_true", help="substring name match")
    t.add_argument("--outcome", action="append", default=[],
                   help="positive, negative or inconclusive (default: all)")
    t.add_argument("--commercial", action="store_true",
                   help="only rows whose licence allows commercial reuse")
    t.add_argument("--limit", type=int, default=50, help="per source")
    t = tsub.add_parser("consensus", help="one relation kind reconciled across sources: ids "
                                          "unified, copies counted once, evidence kept apart")
    t.add_argument("kind")
    t.add_argument("--subject", default="")
    t.add_argument("--object", default="")
    t.add_argument("--min-support", default="",
                   choices=("", "independently_replicated", "documented", "associated",
                            "integrated",
                            "mentioned", "predicted", "signal", "tested_negative",
                            "inconclusive"))
    t.add_argument("--merge-processed", action="store_true",
                   help="treat processed forms (炙黄芪) as the crude drug")
    t.add_argument("--limit", type=int, default=50)
    t = tsub.add_parser("template", help="write a manual dataset's import template: the "
                                         "header row only, or with --demo invented rows "
                                         "that are all pending")
    t.add_argument("dataset", help="hkbu_formulas_manual, hkcmms_manual or hk_cmm_dna_manual")
    t.add_argument("--out", default=".", help="directory to write into (never overwrites)")
    t.add_argument("--demo", action="store_true",
                   help="invented, obviously fictional rows, every one pending review")
    t = tsub.add_parser("verify", help="build, check and look up every manual dataset that "
                                       "has files; exit 0 all passed, 1 a failure, "
                                       "3 nothing to check")
    t.add_argument("dataset", nargs="*", help="limit to these manual datasets")
    t.add_argument("--json", action="store_true")
    t = tsub.add_parser("compare", help="per-source (or per-version) object sets of a subject")
    t.add_argument("kind")
    t.add_argument("subject")
    t.add_argument("--merge-processed", action="store_true")
    t = tsub.add_parser("survey", help="how much the sources of a relation kind copy each other")
    t.add_argument("kind")
    t.add_argument("--sample", type=int, default=300)
    t.add_argument("--min-sources", type=int, default=3)
    t.add_argument("--seed", type=int, default=0)
    t = tsub.add_parser("enrich", help="fetch SymMap/HERB relations per entity (cached)")
    t.add_argument("name", nargs="?", default="", help="a herb name, e.g. 黄芪")
    t.add_argument("--symmap", nargs="*", default=[], help="SymMap ids, e.g. SMHB00187")
    t.add_argument("--herb", nargs="*", default=[], help="HERB ids, e.g. HERB002560")
    t.add_argument("--label", default="Herb", help="HERB entity type of --herb ids")
    t = tsub.add_parser("live", help="a governed call to a live connector")
    t.add_argument("connector")
    t.add_argument("operation")
    t.add_argument("args", nargs="*", help="name=value (values may be JSON)")

    # -- analysis pipelines ---------------------------------------------------
    from .admet import commands as admet_commands
    from .docking import commands as docking_commands
    from .clinic import commands as clinic_commands
    from .omics import commands as omics_commands
    from .structure import commands as structure_commands
    omics_commands.register(sub)
    structure_commands.register(sub)
    clinic_commands.register(sub)
    docking_commands.register(sub)
    admet_commands.register(sub)

    a = ap.parse_args(argv)
    if a.cmd in omics_commands.COMMANDS:
        return omics_commands.dispatch(a)
    if a.cmd in structure_commands.COMMANDS:
        return structure_commands.dispatch(a)
    if a.cmd in clinic_commands.COMMANDS:
        return clinic_commands.dispatch(a)
    if a.cmd in docking_commands.COMMANDS:
        return docking_commands.dispatch(a)
    if a.cmd in admet_commands.COMMANDS:
        return admet_commands.dispatch(a)
    if a.cmd == "research":
        return _cmd_research(a)
    if a.cmd == "snapshots":
        return _cmd_snapshots(a)
    if a.cmd == "validate":
        from .validation.cli import main as validate
        return validate(a.rest)
    if a.cmd == "tcmdb":
        return _cmd_tcmdb(a)

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
    from .acquisition.service import release_of
    from .acquisition.transfer import VerifiedTransfer
    dl = VerifiedTransfer(dest, log=lambda s: print(s, file=sys.stderr))
    try:
        out = dl.fetch(spec.url, spec.filename, checksum=spec.checksum,
                       expected_bytes=spec.expected_bytes, confirm=a.confirm,
                       release=release_of(spec.version), fmt=spec.fmt)
    except Exception as exc:  # noqa: BLE001
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"path": str(out.path), "bytes": out.bytes, "verified": out.verified,
                      "checksum": out.checksum, "from_cache": out.from_cache,
                      **out.record()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
