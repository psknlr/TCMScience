#!/usr/bin/env python
"""Run the tcm.network-pharmacology skill on ledger-recorded snapshots.

    python scripts/build_source_snapshots.py gold --network --raw RAW --out SNAP \\
        --ledger SNAP/audit/snapshots.jsonl
    # optional: the indication's gene set (see scripts/fetch_opentargets.py)
    python scripts/build_source_snapshots.py opentargets \\
        --file RAW/opentargets_MONDO_0005148.json --out SNAP --ledger SNAP/audit/snapshots.jsonl
    python scripts/run_network_pharmacology.py --snapshots SNAP \\
        --ledger SNAP/audit/snapshots.jsonl --out RUN

Writes compounds/targets/enrichment/network tables, claims.json, release.json,
provenance.json, limitations.md and snapshot_lock.json to RUN. ``--lock RUN/snapshot_lock.json``
reruns on exactly those snapshots; ``--formula ID`` analyses another formula version the
herb-layer snapshot records (an id from the formula table, e.g. ``fx:…``). Exit status is non-zero when a snapshot does not
match the ledger, the skill's contract refuses the run, or a claim is refused at release.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.analysis.network_pharmacology import Parameters  # noqa: E402
from bioagent.analysis.skill_runner import SkillRunRefused, run_skill  # noqa: E402
from bioagent.sources.ledger import LedgerError  # noqa: E402
from bioagent.sources.snapshot import SnapshotError  # noqa: E402


def _formula(formula_id: str | None):
    from bioagent.sources.herbs import GEGEN_QINLIAN
    if not formula_id or formula_id == GEGEN_QINLIAN.id:
        return GEGEN_QINLIAN
    from bioagent.sources.formulas import load_formula_table
    record = load_formula_table().by_id(formula_id)
    if record is None:
        raise SkillRunRefused(f"no formula {formula_id!r} in the formula table")
    if not record.resolved:
        raise SkillRunRefused(f"{formula_id} has unresolved ingredients "
                              f"{list(record.unresolved)}; it cannot be studied")
    return record.version


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshots", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skill", default=str(ROOT / "skills" / "tcm" / "network-pharmacology"))
    ap.add_argument("--activity-max-nm", type=float, default=Parameters.activity_max_nm)
    ap.add_argument("--string-min-score", type=float, default=Parameters.string_min_score)
    ap.add_argument("--permutations", type=int, default=Parameters.permutations)
    ap.add_argument("--background", choices=("assayed", "reactome"),
                    default=Parameters.background,
                    help="enrichment background: proteins the compounds were measured "
                         "against, or the whole human Reactome annotation")
    ap.add_argument("--hits", choices=("potency", "screening"), default=Parameters.hits,
                    help="potency: curated measurements at or below the cut-off; "
                         "screening: PubChem active calls weighed against inactive ones")
    ap.add_argument("--screening-min-compounds", type=int,
                    default=Parameters.screening_min_compounds)
    ap.add_argument("--disease-evidence", default=Parameters.disease_evidence,
                    help="Open Targets evidence type defining the disease gene set")
    ap.add_argument("--disease-min-score", type=float, default=Parameters.disease_min_score)
    ap.add_argument("--seed", type=int, default=Parameters.seed)
    ap.add_argument("--allow-source", action="append", default=None, metavar="KEY",
                    help="a source this run may use (repeatable). The run gets the skill's "
                         "request ∩ the enabled source cards ∩ these; without the option "
                         "the last term is unrestricted, and provenance.json says so")
    ap.add_argument("--formula", default=None,
                    help="formula id (default: 葛根芩连汤 of 伤寒论); ids other than the default "
                         "are looked up in the formula table")
    ap.add_argument("--lock", default=None,
                    help="snapshot_lock.json of an earlier run: use exactly those snapshots")
    ap.add_argument("--latest", action="store_true",
                    help="without a lock, take the last recorded snapshot of a source that "
                         "has several (otherwise the run is refused)")
    ap.add_argument("--purpose", choices=("academic", "commercial"), default="academic",
                    help="what the run is for; a commercial run may use only sources whose "
                         "card allows commercial use (NPASS and CMAUP are academic-only)")
    ap.add_argument("--ledger-head-from", default="", metavar="PROVENANCE",
                    help="a previous run's provenance.json: refuse this run if the ledger no "
                         "longer holds the entry that run read up to (cut short, rolled back "
                         "or rewritten since)")
    ap.add_argument("--allow-ungoverned", action="store_true",
                    help="run even when PSH is not importable (provenance records governed: false)")
    args = ap.parse_args(argv)
    params = Parameters(activity_max_nm=args.activity_max_nm,
                        string_min_score=args.string_min_score,
                        permutations=args.permutations, seed=args.seed,
                        background=args.background, hits=args.hits,
                        screening_min_compounds=args.screening_min_compounds,
                        disease_evidence=args.disease_evidence,
                        disease_min_score=args.disease_min_score)
    head = None
    if args.ledger_head_from:
        try:
            earlier = json.loads(Path(args.ledger_head_from).read_text(encoding="utf-8"))
            head = earlier["ledger_head"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"refused: no ledger head in {args.ledger_head_from} ({exc!r})", file=sys.stderr)
            return 1
    try:
        formula = _formula(args.formula)
        provenance = run_skill(skill_dir=args.skill, snapshot_root=args.snapshots,
                               formula=formula, lock=args.lock, latest=args.latest,
                               ledger_path=args.ledger, out_dir=args.out, params=params,
                               allowed=set(args.allow_source) if args.allow_source else None,
                               purpose=args.purpose, expected_ledger_head=head,
                               require_psh=not args.allow_ungoverned)
    except (SkillRunRefused, SnapshotError, LedgerError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: provenance[k] for k in ("dataset_hashes", "excluded", "background", "network",
                                                "disease", "claims", "psh_program_fingerprint", "governed",
                                                "result_digest")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
