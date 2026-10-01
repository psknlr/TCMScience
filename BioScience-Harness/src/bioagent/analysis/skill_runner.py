"""Run the network-pharmacology skill end to end, under its contract.

1. read the skill contract (``skill.yaml``);
2. load every snapshot through the ledger, and keep only the sources the contract is
   granted (request ∩ enabled source cards ∩ run allowance);
3. compile the skill into a PSH ``ScientificProgram`` — the compiler checks the steps'
   study designs against the claim kind. PSH is required: a run without it is refused
   unless the caller passes ``require_psh=False``, and the provenance record then says
   ``governed: false`` (an earlier version skipped the compile silently on ImportError);
4. run the analysis and the release check;
5. write the outputs and a provenance record.

The intervention is a parameter: any ``FormulaVersion`` the herb-layer snapshot records
(the 葛根芩连汤 of 伤寒论 is only the default). A formula the snapshot does not record is
refused rather than analysed as an empty composition.

The inputs are pinned. Every run writes ``snapshot_lock.json`` (each source's exact
snapshot id); a run given a lock uses exactly those snapshots and refuses if one is no
longer in the ledger, so a later import cannot silently change a study's inputs. Without a
lock, a source with several recorded snapshots is refused unless the caller asks for the
latest (``latest=True``): "whichever was recorded last" is not a reproducible input.

The provenance record is what ``ProvenanceCapsule`` asks for and nothing filled before:
snapshot ids (``dataset_hashes``), parameters and seed (``random_seed``), a digest of the
analysis code, the PSH program fingerprint, and a digest of the result.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..providers.skills import SkillContract
from ..sources.herbs import GEGEN_QINLIAN, KEY as HERB_LAYER, FormulaVersion
from ..sources.ledger import SnapshotLedger
from ..sources.snapshot import load_snapshot
from .network_pharmacology import NetworkPharmacologyResult, Parameters, run_network_pharmacology

__all__ = ["run_skill", "SkillRunRefused", "read_lock", "LOCK_FILE"]

LOCK_FILE = "snapshot_lock.json"


class SkillRunRefused(RuntimeError):
    """The run cannot proceed under the skill's contract, or its claims were refused."""


def _recorded(ledger: SnapshotLedger) -> dict[str, list[tuple[str, str]]]:
    """Every (version, snapshot id) the ledger records, per source, in ledger order."""
    out: dict[str, list[tuple[str, str]]] = {}
    for e in ledger.entries():
        pair = (e.version, e.snapshot_id)
        if pair not in out.setdefault(e.key, []):
            out[e.key].append(pair)
    return out


def read_lock(path: str | Path) -> dict[str, str]:
    """A ``snapshot_lock.json``: source key -> snapshot id."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("snapshots"), dict):
        raise SkillRunRefused(f"{path} is not a snapshot lock (schema 1)")
    return {str(k): str(v) for k, v in data["snapshots"].items()}


def _choose(recorded: dict[str, list[tuple[str, str]]], wanted: list[str],
            lock: dict[str, str] | None, latest: bool) -> dict[str, tuple[str, str]]:
    chosen: dict[str, tuple[str, str]] = {}
    missing = [k for k in wanted if k not in recorded]
    if missing:
        raise SkillRunRefused(f"no snapshot recorded for {missing}; build them first")
    for key in wanted:
        if lock is not None:
            if key not in lock:
                raise SkillRunRefused(f"the lock pins no snapshot for {key!r}")
            match = [p for p in recorded[key] if p[1] == lock[key]]
            if not match:
                raise SkillRunRefused(
                    f"the locked snapshot {lock[key]} is not in the ledger; the study's "
                    "inputs cannot be reproduced (start a new analysis version instead)")
            chosen[key] = match[0]
        elif len(recorded[key]) > 1 and not latest:
            raise SkillRunRefused(
                f"{key} has {len(recorded[key])} recorded snapshots "
                f"({', '.join(p[1] for p in recorded[key])}); pass a lock naming one, "
                "or latest=True to take the last recorded")
        else:
            chosen[key] = recorded[key][-1]
    return chosen


def _tsv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> str:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([";".join(map(str, r[c])) if isinstance(r.get(c), (list, tuple))
                        else r.get(c) for c in columns])
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def run_skill(*, skill_dir: str | Path, snapshot_root: str | Path, ledger_path: str | Path,
              out_dir: str | Path, params: Parameters = Parameters(),
              allowed: set[str] | None = None, accept_review: bool = False,
              require_psh: bool = True, formula: FormulaVersion = GEGEN_QINLIAN,
              lock: str | Path | dict[str, str] | None = None,
              latest: bool = False) -> dict[str, Any]:
    contract = SkillContract.load(Path(skill_dir) / "skill.yaml")
    ledger = SnapshotLedger(ledger_path)
    ledger.verify()
    granted, refused = contract.grant(allowed=allowed)
    wanted = [HERB_LAYER, *sorted(granted)]
    pinned = lock if isinstance(lock, dict) or lock is None else read_lock(lock)
    chosen = _choose(_recorded(ledger), wanted, pinned, latest)
    snapshots = [load_snapshot(snapshot_root, key, chosen[key][0], ledger=ledger,
                               accept_review=accept_review) for key in wanted]
    for snap in snapshots:
        if snap.snapshot_id != chosen[snap.key][1]:
            raise SkillRunRefused(f"{snap.key}: loaded {snap.snapshot_id}, but the run "
                                  f"pins {chosen[snap.key][1]}")
    herbs = next(s for s in snapshots if s.key == HERB_LAYER)
    if not any(e.get("subject") == formula.id and e.get("predicate") == "contains"
               for e in herbs.edges):
        raise SkillRunRefused(
            f"the herb-layer snapshot {herbs.snapshot_id} records no composition for "
            f"{formula.id} ({formula.chinese}); build it with that formula first")

    compiled = None
    try:
        from psh.policy import PolicySnapshot
        from psh.workflow import ScientificCompiler

        from ..psh.skill_program import ClaimScope, skill_program
    except ImportError as exc:
        if require_psh:
            raise SkillRunRefused(
                "PSH is not importable, so the skill's program cannot be compiled and "
                "its claims cannot be checked against their study designs; install "
                "PSH-Harness or pass require_psh=False for an ungoverned run "
                f"({exc})") from exc
    else:
        scope = ClaimScope(population="human proteins (in silico)",
                           intervention=f"{formula.chinese} ({formula.source})",
                           outcome="Reactome pathway over-representation")
        policy = PolicySnapshot(profile_id="tcm-network-pharmacology",
                                require_claim_support=False)
        program = skill_program(contract, scope,
                                provenance=tuple(s.snapshot_id for s in snapshots))
        compiled = ScientificCompiler().compile(program, policy.envelope(), policy=policy)

    result: NetworkPharmacologyResult = run_network_pharmacology(
        snapshots, formula=formula, params=params, contract=contract)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / LOCK_FILE).write_text(json.dumps(
        {"schema": 1, "snapshots": {s.key: s.snapshot_id for s in snapshots}},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = {
        "compounds.tsv": _tsv(out / "compounds.tsv", result.compounds,
                              ["compound", "name", "level", "herbs", "sources"]),
        "targets.tsv": _tsv(out / "targets.tsv", result.targets,
                            ["target", "name", "measurements", "in_background", "disease_score",
                             "compounds"]),
        "enrichment.tsv": _tsv(out / "enrichment.tsv", result.enrichment,
                               ["pathway", "name", "size", "in_background", "overlap", "p_value", "q_value",
                                "empirical_p", "fold_enrichment", "targets"]),
        "network.tsv": _tsv(out / "network.tsv", result.network["hubs"],
                            ["target", "name", "degree", "betweenness"]),
    }
    payloads = [("claims.json", result.claims), ("release.json", result.release)]
    if result.disease:
        payloads.append(("disease.json", result.disease))
    for name, payload in payloads:
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        files[name] = "sha256:" + hashlib.sha256((out / name).read_bytes()).hexdigest()
    (out / "limitations.md").write_text(
        "# Limitations\n\n" + "\n".join(f"- {line}" for line in result.limitations) + "\n",
        encoding="utf-8")
    provenance = {
        "skill": {"id": contract.id, "version": contract.version,
                  "max_claim_kind": contract.max_claim_kind},
        "formula": {"id": formula.id, "chinese": formula.chinese, "source": formula.source,
                    "fingerprint": formula.fingerprint},
        "snapshot_lock": {s.key: s.snapshot_id for s in snapshots},
        "lock_given": pinned is not None,
        "dataset_hashes": result.snapshots,
        "sources_refused": refused,
        # The third term of request ∩ enabled cards ∩ run allowance. ``None`` is not "no
        # sources"; it is "no allowance was given", said out loud so a reader does not
        # take a two-way intersection for the three-way one the contract describes.
        "source_allowance": sorted(allowed) if allowed is not None else "unrestricted",
        "parameters": asdict(params), "random_seed": params.seed,
        "code_digest": result.code_digest,
        "psh_program_fingerprint": compiled.fingerprint if compiled else None,
        "governed": compiled is not None,
        "result_digest": result.digest(),
        "outputs": files,
        "excluded": result.excluded,
        "network": {k: v for k, v in result.network.items() if k != "hubs"},
        "background": result.background,
        "disease": {k: v for k, v in result.disease.items() if k != "targets"},
        "claims": {"candidates": len(result.claims),
                   "released": len(result.release["released"]),
                   "refused": len(result.release["refused"])},
        "python": platform.python_version(), "finished_at": time.time(),
    }
    (out / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    if result.release["refused"]:
        raise SkillRunRefused(f"{len(result.release['refused'])} claim(s) refused at release; "
                              f"see {out / 'release.json'}")
    return provenance
