"""Run the network-pharmacology skill end to end, under its contract.

1. read the skill contract (``skill.yaml``);
2. load every snapshot through the ledger, and keep only the sources the contract is
   granted (request ∩ enabled source cards ∩ run allowance);
3. compile the skill into a PSH ``ScientificProgram`` — the compiler checks the steps'
   study designs against the claim kind, and each step's reach against the run's
   envelope (every step runs inside the analysis, in this process, on snapshots it reads
   from disk). PSH is required: a run without it is refused unless the caller passes
   ``require_psh=False``, and the provenance record then says ``governed: false`` (an
   earlier version skipped the compile silently on ImportError);
4. for a commercial purpose, put everything the run draws on — the analysis code and the
   libraries it reads its inputs with, PSH, each source snapshot — to the usage gate
   (``bioagent.licences``), as a commercial ``Runtime.invoke`` does, and refuse the run
   when an asset has no reviewed record permitting it. A skill run used to apply only the
   source cards and the composition licence, so code nobody had reviewed passed unasked;
5. run the analysis and the release check;
6. write the outputs and a provenance record.

The intervention is a parameter: any ``FormulaVersion`` the herb-layer snapshot records
(the 葛根芩连汤 of 伤寒论 is only the default). A formula the snapshot does not record is
refused rather than analysed as an empty composition, and so is one whose composition is
not the one the snapshot records: the analysis reads the composition from the snapshot, so
a record edited after the build would be reported as a composition the run never analysed.
The herb layer takes part in every run, so the purpose check on the skill's sources does
not cover it: a commercial run also needs the studied composition record itself licensed
for commercial use, and a formula from a table whose licence is unstated is refused.

The inputs are pinned. Only snapshots of the release ``skill.yaml`` names count
(``npass@2.0`` is release 2.0 or a scoped build of it). Every run writes
``snapshot_lock.json`` (each source's exact snapshot id); a run given a lock uses exactly
those snapshots and refuses if one is no longer in the ledger, so a later import cannot
silently change a study's inputs. Without a lock, a source with several recorded snapshots
of that release is refused unless the caller asks for the latest (``latest=True``):
"whichever was recorded last" is not a reproducible input. A run also records the ledger
head it read; given an earlier run's head, it refuses a ledger cut short since.

The provenance record is what ``ProvenanceCapsule`` asks for and nothing filled before:
snapshot ids (``dataset_hashes``), parameters and seed (``random_seed``), a digest of the
analysis code, the PSH program fingerprint, and a digest of the result.

The outputs are the nine exports of :mod:`bioagent.analysis.np_exports` (compounds,
targets, disease targets, enrichment, the network's edge list, provenance with every edge
traced to its snapshot record, limitations, report.html and ``run_manifest.json`` with the
sixteen workflow stages), plus ``claims.json``, ``release.json``, ``disease.json`` (with a
disease) and ``snapshot_lock.json``. None carries a timestamp: two runs on the same
snapshots and ledger write the same bytes, and so does the command the manifest records to
reproduce the run (``reproduce_exact`` says whether it carries every choice the run made).
Whether this execution was given a lock is recorded in the manifest's ``execution`` block,
not in ``provenance.json``: the reproducing command passes one, and the exports must not
differ for it. ``provenance.json`` lists the digests of the skill run's own records
(claims, release, disease); every export's digest is in ``run_manifest.json``.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from ..providers.skills import SkillContract
from ..sources.herbs import GEGEN_QINLIAN, KEY as HERB_LAYER, FormulaVersion
from ..sources.ledger import SnapshotLedger
from ..sources.snapshot import Snapshot, load_snapshot
from ..tcmdb.spec import allows_commercial
from .network_pharmacology import (FormulaNotRecorded, NetworkPharmacologyResult, Parameters,
                                    recorded_composition, run_network_pharmacology)
from .np_exports import write_exports, write_manifest

__all__ = ["run_skill", "SkillRunRefused", "read_lock", "LOCK_FILE"]

LOCK_FILE = "snapshot_lock.json"


class SkillRunRefused(RuntimeError):
    """The run cannot proceed under the skill's contract, or its claims were refused."""


def _analysis_manifest(snapshot_root: str | Path, ledger_path: str | Path) -> Any:
    """The analysis as a component: what it runs, imports and reads.

    The contract's tools (``np.composition``, ``np.pathway_enrichment`` …) are stages of
    :func:`run_network_pharmacology`, which this run calls in process on snapshots it reads
    from disk with pyarrow, under a contract it reads with PyYAML. So every step reaches
    what the run reaches — local compute, reading the snapshot store and the ledger — and
    that, not an assumption, is what the program declares and what the usage gate rules on.
    """
    from ..runtime.component import (ComponentManifest, LicenseSpec, Permissions,
                                     Requirements, RuntimeSpec)

    return ComponentManifest(
        id="analysis.network_pharmacology", kind="tool", name="network pharmacology analysis",
        runtime=RuntimeSpec(backend="python", deterministic=True,
                            entrypoint="bioagent.analysis.network_pharmacology:"
                                       "run_network_pharmacology"),
        requires=Requirements(python=("pyarrow", "yaml")),
        license=LicenseSpec(spdx="MIT", integration_mode="native"),
        permissions=Permissions(filesystem_read=(str(Path(snapshot_root).resolve()),
                                                 str(Path(ledger_path).resolve()))))


def _analysis_components(contract: SkillContract, snapshot_root: str | Path,
                         ledger_path: str | Path) -> dict[str, Any]:
    """The component each step's tool names: the analysis itself, as the bridge admits it."""
    from ..psh.manifest import bridge_manifest

    admitted = bridge_manifest(_analysis_manifest(snapshot_root, ledger_path))
    return {tool: admitted for tool in contract.tools}


def _usage(analysis: Any, chosen: Mapping[str, tuple[str, str]], sources: Mapping[str, str],
           governed: bool, purpose: str, licences: Any) -> Any:
    """The usage gate's ruling on everything this run draws on, as ``Runtime.invoke`` asks it
    for a component; ``None`` for a research run, which the gate does not rule on.

    What the run draws on: the analysis (its code, by import root, and the libraries it
    reads the inputs with), PSH when it compiled the program, and each source snapshot it
    uses. The herb layer is not a source card; the studied composition's own licence is
    ruled on separately (``_composition_licences``).
    """
    if purpose == "academic":
        return None
    from ..licences import Asset, LicenceRecords, assets_for, usage_decision

    records = licences if licences is not None else LicenceRecords.load()
    assets = list(assets_for(analysis, records=records))
    if governed:
        assets.append(Asset("code", "psh", "requirement"))
    assets += [Asset("data", f"source:{key}@{chosen[key][0]}", "source")
               for key in sorted(sources)]
    return usage_decision(assets, purpose=purpose, records=records)


def _fits(version: str, pin: str) -> bool:
    """Whether a recorded snapshot ``version`` is one the contract's ``pin`` names.

    ``2.0`` names release 2.0 and any scoped build of it (``2.0+subset-…``); a pin with a
    scope (``26.09+MONDO_0005148``) names that scope only; a bare key names any version.
    Without this, a skill that asked for ``npass@2.0`` ran on whichever NPASS build was
    recorded last.
    """
    return pin == "latest-approved" or version == pin or version.startswith(pin + "+")


def _recorded(ledger: SnapshotLedger) -> dict[str, list[tuple[str, str]]]:
    """Every (version, snapshot id) the ledger records, per source, in ledger order."""
    out: dict[str, list[tuple[str, str]]] = {}
    for e in ledger.entries():
        pair = (e.version, e.snapshot_id)
        pairs = out.setdefault(e.key, [])
        if pair in pairs:                  # re-recorded: it is now the latest
            pairs.remove(pair)
        pairs.append(pair)
    return out


def read_lock(path: str | Path) -> dict[str, str]:
    """A ``snapshot_lock.json``: source key -> snapshot id."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("snapshots"), dict):
        raise SkillRunRefused(f"{path} is not a snapshot lock (schema 1)")
    return {str(k): str(v) for k, v in data["snapshots"].items()}


def _choose(recorded: dict[str, list[tuple[str, str]]], wanted: list[str],
            lock: dict[str, str] | None, latest: bool,
            pins: dict[str, str] | None = None) -> dict[str, tuple[str, str]]:
    """One (version, snapshot id) per wanted source: the lock's, else the only one the
    skill's pin names, else (``latest``) the last recorded of those."""
    pins = pins or {}
    chosen: dict[str, tuple[str, str]] = {}
    missing = [k for k in wanted if k not in recorded]
    if missing:
        raise SkillRunRefused(f"no snapshot recorded for {missing}; build them first")
    if lock is not None and set(lock) - set(wanted):
        raise SkillRunRefused(f"the lock pins {sorted(set(lock) - set(wanted))}, which this run "
                              "does not use; a lock describes exactly one run's inputs")
    for key in wanted:
        pin = pins.get(key, "latest-approved")
        fitting = [p for p in recorded[key] if _fits(p[0], pin)]
        if lock is not None:
            if key not in lock:
                raise SkillRunRefused(f"the lock pins no snapshot for {key!r}")
            match = [p for p in recorded[key] if p[1] == lock[key]]
            if not match:
                raise SkillRunRefused(
                    f"the locked snapshot {lock[key]} is not in the ledger; the study's "
                    "inputs cannot be reproduced (start a new analysis version instead)")
            if match[0] not in fitting:
                raise SkillRunRefused(
                    f"the locked snapshot {lock[key]} is {key}@{match[0][0]}, which the "
                    f"skill's pin {key}@{pin} does not name")
            chosen[key] = match[0]
        elif not fitting:
            versions = ", ".join(sorted({p[0] for p in recorded[key]}))
            raise SkillRunRefused(
                f"no snapshot recorded for {key}@{pin} (recorded: {versions}); build it "
                "first, or change the skill's pin")
        elif len(fitting) > 1 and not latest:
            raise SkillRunRefused(
                f"{key} has {len(fitting)} recorded snapshots "
                f"({', '.join(p[1] for p in fitting)}); pass a lock naming one, "
                "or latest=True to take the last recorded")
        else:
            chosen[key] = fitting[-1]
    return chosen


def _datatypes(text: str) -> str | tuple[str, ...]:
    """``--disease-evidence`` as scripts/run_network_pharmacology.py reads it."""
    names = tuple(p.strip() for p in str(text).split(",") if p.strip())
    return names[0] if len(names) == 1 else names


def _default_contract(contract: SkillContract) -> bool:
    """Whether ``contract`` is the one the script loads when given no ``--skill``."""
    from ..config import skills_dir
    try:
        default = SkillContract.load(skills_dir() / "tcm" / "network-pharmacology" / "skill.yaml")
    except Exception:                                           # noqa: BLE001
        return False
    return default.as_dict() == contract.as_dict()


def _reproduce(formula: FormulaVersion, params: Parameters, allowed: set[str] | None,
               purpose: str, *, contract: SkillContract, governed: bool,
               accept_review: bool) -> tuple[list[str], bool]:
    """The command that runs this analysis again on the same snapshots (``OUT`` is this
    run's directory, whose ``snapshot_lock.json`` pins them), and whether it reproduces
    exactly this run. It cannot carry the parameters the script has no option for (pathway
    sizes, FDR, screening thresholds other than the minimum compounds), another skill
    directory, ``accept_review``, or a formula it cannot look up by id (an HKBU formula
    needs ``--hkbu-formulas``, a path on this machine); with any of them not at the
    script's default the command is recorded with ``reproduce_exact`` false, and the
    manifest's ``parameters`` and ``formula`` are the record."""
    argv = ["python", "scripts/run_network_pharmacology.py", "--snapshots", "SNAPSHOTS",
            "--ledger", "LEDGER", "--out", "OUT2", "--lock", "OUT/snapshot_lock.json",
            "--formula", formula.id, "--activity-max-nm", f"{params.activity_max_nm:g}",
            "--string-min-score", f"{params.string_min_score:g}",
            "--permutations", str(params.permutations), "--background", params.background,
            "--hits", params.hits, "--screening-min-compounds",
            str(params.screening_min_compounds),
            "--disease-evidence", ",".join(params.evidence_datatypes),
            "--disease-min-score", f"{params.disease_min_score:g}", "--seed", str(params.seed),
            "--purpose", purpose]
    for key in sorted(allowed or ()):
        argv += ["--allow-source", key]
    if not governed:
        argv.append("--allow-ungoverned")

    def value(flag: str) -> str:
        return argv[argv.index(flag) + 1]

    # the parameters the script would build from this command, read back as it reads them
    try:
        again = Parameters(activity_max_nm=float(value("--activity-max-nm")),
                           string_min_score=float(value("--string-min-score")),
                           permutations=int(value("--permutations")), seed=int(value("--seed")),
                           background=value("--background"), hits=value("--hits"),
                           screening_min_compounds=int(value("--screening-min-compounds")),
                           disease_evidence=_datatypes(value("--disease-evidence")),
                           disease_min_score=float(value("--disease-min-score")))
    except ValueError:
        again = None
    looked_up = formula == GEGEN_QINLIAN or formula.id.startswith("tcm:formula.fx")
    # an empty allowance cannot be written as options: without one the script's is unrestricted
    exact = (again == params and looked_up and not accept_review
             and (allowed is None or bool(allowed)) and _default_contract(contract))
    return argv, exact


def _composition_licences(herbs: Snapshot, formula: FormulaVersion, purpose: str) -> list[str]:
    """The licences of ``formula``'s composition record in the herb layer, once it is
    known to be the record the snapshot holds and, for a commercial run, one that allows
    commercial use."""
    try:
        edges = recorded_composition(herbs, formula)
    except FormulaNotRecorded as exc:
        raise SkillRunRefused(str(exc)) from exc
    licences = sorted({str(e.get("license") or "") for e in edges})
    barred = [lic or "no stated licence" for lic in licences if not allows_commercial(lic)]
    if purpose == "commercial" and barred:
        raise SkillRunRefused(
            f"a commercial run needs commercial use allowed for the composition it studies; "
            f"{formula.id} ({formula.chinese}) is recorded under {', '.join(barred)}, which "
            "does not say so")
    return licences


def run_skill(*, skill_dir: str | Path, snapshot_root: str | Path, ledger_path: str | Path,
              out_dir: str | Path, params: Parameters = Parameters(),
              allowed: set[str] | None = None, accept_review: bool = False,
              require_psh: bool = True, formula: FormulaVersion = GEGEN_QINLIAN,
              lock: str | Path | dict[str, str] | None = None,
              latest: bool = False, purpose: str = "academic",
              expected_ledger_head: Mapping[str, Any] | None = None,
              licences: Any = None) -> dict[str, Any]:
    """Run the skill under its contract; the provenance record it writes.

    ``purpose`` is ``academic`` or ``commercial`` (``sources.cards.PURPOSES``). A commercial
    run is also put to the usage gate, as a commercial ``Runtime.invoke`` is: every asset it
    draws on needs a reviewed record permitting that use (``licences``, by default the
    shipped ``registry/licence_records.yaml``), the analysis does not run if one is
    refused, and the decision is recorded as ``provenance["usage"]``. A research run is
    ruled on as it always was and records nothing new.
    """
    contract = SkillContract.load(Path(skill_dir) / "skill.yaml")
    ledger = SnapshotLedger(ledger_path)
    # ``expected_ledger_head`` is the head an earlier run recorded: the ledger must still
    # hold that entry as it was, i.e. it has only been appended to since.
    ledger.verify(expected_head=expected_ledger_head)
    # The ledger entry this run reads up to, taken before it picks its snapshots. A chain
    # cannot see its own end cut off; a later ``verify(expected_head=...)`` with this can.
    head = ledger.head()
    granted, refused = contract.grant(allowed=allowed, purpose=purpose)
    wanted = [HERB_LAYER, *sorted(granted)]
    pinned = lock if isinstance(lock, dict) or lock is None else read_lock(lock)
    chosen = _choose(_recorded(ledger), wanted, pinned, latest,
                     pins={HERB_LAYER: "latest-approved", **granted})
    snapshots = [load_snapshot(snapshot_root, key, chosen[key][0], ledger=ledger,
                               accept_review=accept_review) for key in wanted]
    for snap in snapshots:
        if snap.snapshot_id != chosen[snap.key][1]:
            raise SkillRunRefused(
                f"{snap.key}: the run pins {chosen[snap.key][1]}, but version "
                f"{chosen[snap.key][0]} on disk now holds {snap.snapshot_id}: the pinned build "
                "was replaced and cannot be reproduced from this snapshot store")
    herbs = next(s for s in snapshots if s.key == HERB_LAYER)
    composition_licences = _composition_licences(herbs, formula, purpose)
    analysis = _analysis_manifest(snapshot_root, ledger_path)

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
        envelope = policy.envelope()
        program = skill_program(contract, scope,
                                components=_analysis_components(contract, snapshot_root,
                                                                ledger_path),
                                envelope=envelope,
                                provenance=tuple(s.snapshot_id for s in snapshots))
        compiled = ScientificCompiler().compile(program, envelope, policy=policy)

    usage = _usage(analysis, chosen, granted, compiled is not None, purpose, licences)
    if usage is not None and not usage.allowed:
        raise SkillRunRefused(f"the usage gate refused this {purpose} run: {usage.reason}")

    result: NetworkPharmacologyResult = run_network_pharmacology(
        snapshots, formula=formula, params=params, contract=contract)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / LOCK_FILE).write_text(json.dumps(
        {"schema": 1, "snapshots": {s.key: s.snapshot_id for s in snapshots}},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files: dict[str, str] = {}
    extra: dict[str, dict[str, Any]] = {}
    payloads = [("claims.json", result.claims), ("release.json", result.release)]
    if result.disease:
        payloads.append(("disease.json", {k: v for k, v in result.disease.items()
                                          if k != "genes"}))
    for name, payload in payloads:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        (out / name).write_bytes(data)
        files[name] = "sha256:" + hashlib.sha256(data).hexdigest()
        extra[name] = {"sha256": files[name], "bytes": len(data)}
    lock = (out / LOCK_FILE).read_bytes()
    extra[LOCK_FILE] = {"sha256": "sha256:" + hashlib.sha256(lock).hexdigest(),
                        "bytes": len(lock)}
    provenance = {
        "skill": {"id": contract.id, "version": contract.version,
                  "max_claim_kind": contract.max_claim_kind},
        "formula": {"id": formula.id, "chinese": formula.chinese, "source": formula.source,
                    "fingerprint": formula.fingerprint, "license": composition_licences},
        "snapshot_lock": {s.key: s.snapshot_id for s in snapshots},
        "dataset_hashes": result.snapshots,
        "sources_refused": refused,
        # The third term of request ∩ enabled cards ∩ run allowance. ``None`` is not "no
        # sources"; it is "no allowance was given", said out loud so a reader does not
        # take a two-way intersection for the three-way one the contract describes.
        "source_allowance": sorted(allowed) if allowed is not None else "unrestricted",
        "purpose": purpose,
        "ledger_head": head,
        "parameters": asdict(params), "random_seed": params.seed,
        "code_digest": result.code_digest,
        "psh_program_fingerprint": compiled.fingerprint if compiled else None,
        "governed": compiled is not None,
        "result_digest": result.digest(),
        # the skill run's own records, as written; every export's digest (these included)
        # is in run_manifest.json, which is written after provenance.json and so can list it
        "outputs": dict(files),
        "excluded": result.excluded,
        "network": {k: v for k, v in result.network.items() if k not in ("hubs", "edge_list")},
        "background": result.background,
        "disease": {k: v for k, v in result.disease.items() if k not in ("targets", "genes")},
        "claims": {"candidates": len(result.claims),
                   "released": len(result.release["released"]),
                   "refused": len(result.release["refused"])},
        # no wall-clock time: the record is an export, and two runs must give its bytes
        "python": platform.python_version(),
    }
    if usage is not None:
        # every asset, the record and terms relied on, each verdict: what a commercial
        # Runtime.invoke records on its policy event
        provenance["usage"] = usage.as_dict()
    data = json.loads(json.dumps(result.as_dict(), ensure_ascii=False, default=str))
    data["digest"] = provenance["result_digest"]
    argv, exact = _reproduce(formula, params, allowed, purpose, contract=contract,
                             governed=compiled is not None, accept_review=accept_review)
    exports = write_exports(
        data, out, formula=formula, snaps=snapshots, provenance=provenance,
        requested=wanted, refused=refused, reproduce=argv,
        manifest={"runner": "skill", "skill": provenance["skill"],
                  "program_fingerprint": provenance["psh_program_fingerprint"],
                  "governed": provenance["governed"], "purpose": purpose,
                  "reproduce_exact": exact})
    # the returned record is provenance.json's run-level part, as written: nothing is added
    # to it after the file is serialised
    write_manifest(exports, outputs=extra,
                   execution={"ledger_head": head, "lock_given": pinned is not None})
    if result.release["refused"]:
        raise SkillRunRefused(f"{len(result.release['refused'])} claim(s) refused at release; "
                              f"see {out / 'release.json'}")
    return provenance
