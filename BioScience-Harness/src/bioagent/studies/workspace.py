"""The study directory: every input, decision and result of one study, in fixed places.

::

    study/
      protocol.yaml          question, hypothesis, falsifier, original designs, validation
                             plan, artifacts, and the protocol hash
      cohort_manifest.tsv    one row per sample (subject, dataset, condition, batch, role…)
      contrasts.tsv          the prespecified contrasts and their test families
      snapshot_lock.json     exact data snapshots used
      environment.lock       interpreter, numpy, platform, code commit
      qc/                    gate and audit reports
      discovery/  validation/  sensitivity/
      claims.json            ClaimRecords with their downgrades
      provenance.json        fingerprints of the inputs at creation
      limitations.md         what the gate and the audit found, in words

``verify_workspace`` re-hashes the inputs: an edited manifest or contrast table after
creation makes the study exploratory and says which file changed.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import yaml

from .audit import AuditReport
from .contract import (ClaimRecord, CohortManifest, ContrastSpec, OmicsArtifact, StudySpec,
                       ValidationPlan, contrasts_tsv, read_contrasts)
from .design import fingerprint
from .gate import GateResult

__all__ = ["create_workspace", "verify_workspace", "write_claims", "SUBDIRS"]

SUBDIRS = ("qc", "discovery", "validation", "sensitivity")


def _plain(x: Any) -> Any:
    if isinstance(x, Mapping):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def _git_commit() -> str:
    """HEAD, marked ``+dirty`` when the code under it has uncommitted changes."""
    here = Path(__file__).parent
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10, cwd=here).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", str(here.parent)],
                               capture_output=True, text=True, timeout=10,
                               cwd=here).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{head}+dirty" if head and dirty else head


def create_workspace(root: str | Path, spec: StudySpec, manifest: CohortManifest,
                     contrasts: Sequence[ContrastSpec], plan: ValidationPlan,
                     artifacts: Sequence[OmicsArtifact] = (), *,
                     snapshot_lock: Mapping[str, str] | None = None,
                     gate: GateResult | None = None, audit: AuditReport | None = None,
                     overwrite: bool = False) -> Path:
    root = Path(root)
    if (root / "protocol.yaml").exists() and not overwrite:
        raise FileExistsError(f"{root} already holds a study; a protocol is not rewritten "
                              "in place (pass overwrite=True for a new study)")
    root.mkdir(parents=True, exist_ok=True)
    for d in SUBDIRS:
        (root / d).mkdir(exist_ok=True)
    protocol = spec.protocol(contrasts, plan).lock()
    (root / "protocol.yaml").write_text(yaml.safe_dump(_plain({
        "study": asdict(spec), "validation_plan": asdict(plan),
        "artifacts": [asdict(a) for a in artifacts],
        "protocol_hash": protocol.locked}), sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    (root / "cohort_manifest.tsv").write_text(manifest.to_tsv(), encoding="utf-8")
    (root / "contrasts.tsv").write_text(contrasts_tsv(contrasts), encoding="utf-8")
    (root / "snapshot_lock.json").write_text(json.dumps(
        {"schema": 1, "snapshots": dict(snapshot_lock or {})}, indent=2), encoding="utf-8")
    (root / "environment.lock").write_text(
        f"python={sys.version.split()[0]}\nnumpy={np.__version__}\n"
        f"platform={platform.platform()}\ncode_commit={_git_commit()}\n", encoding="utf-8")
    if gate is not None:
        (root / "qc" / "design_gate.json").write_text(json.dumps(gate.as_dict(), indent=2,
                                                                 ensure_ascii=False))
    if audit is not None:
        (root / "qc" / "audit.json").write_text(json.dumps(audit.as_dict(), indent=2,
                                                           ensure_ascii=False, default=str))
        (root / "qc" / "audit.md").write_text(audit.to_markdown(), encoding="utf-8")
    (root / "claims.json").write_text("[]\n", encoding="utf-8")
    prov = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "protocol_hash": protocol.locked,
            "inputs": {name: fingerprint((root / name).read_text(encoding="utf-8"))
                       for name in ("protocol.yaml", "cohort_manifest.tsv", "contrasts.tsv",
                                    "snapshot_lock.json")}}
    (root / "provenance.json").write_text(json.dumps(prov, indent=2), encoding="utf-8")
    lines = [f"# Limitations: {spec.title}", ""]
    if gate is not None:
        lines += [f"Design gate: **{gate.status}**", ""]
        lines += [f"- missing: {m}" for m in gate.missing]
        lines += [f"- downgraded: {d}" for d in gate.downgrades]
        lines += [f"- note: {n}" for n in gate.notes]
        lines.append("")
    if audit is not None:
        lines += [audit.to_markdown()]
    for o in spec.original:
        lines.append(f"- {o.accession}: original design {o.design}"
                     f"{', randomised' if o.randomized else ', not randomised'}; "
                     f"sample unit {o.sample_unit}{', pooled libraries' if o.pooled else ''}")
        lines += [f"  - {n}" for n in o.notes]
    (root / "limitations.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def verify_workspace(root: str | Path) -> dict:
    """Which inputs changed since creation; a changed input makes the study exploratory."""
    root = Path(root)
    prov = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    changed = [name for name, fp in prov["inputs"].items()
               if fingerprint((root / name).read_text(encoding="utf-8")) != fp]
    # the files must still parse into the contract objects
    CohortManifest.read(root / "cohort_manifest.tsv")
    read_contrasts((root / "contrasts.tsv").read_text(encoding="utf-8"))
    return {"changed": changed, "status": "exploratory" if changed else "as_registered",
            "protocol_hash": prov["protocol_hash"]}


def write_claims(root: str | Path, claims: Sequence[ClaimRecord]) -> None:
    Path(root, "claims.json").write_text(json.dumps([c.as_dict() for c in claims], indent=2,
                                                    ensure_ascii=False) + "\n",
                                         encoding="utf-8")
