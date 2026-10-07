"""`draft-tcm-prescription` — differentiation and a draft prescription as a governed skill.

Runs :func:`bioagent.clinic.session.assess` on a 四诊 intake. The draft is an output for a
licensed practitioner to sign; the skill never signs, and its manifest asks for approval
before it acts. The one claim it can make is an attribution of the textbooks: that the
findings recorded meet 《中医诊断学》's criteria for a syndrome, for which 《方剂学》 gives a
base formula. That is a ``traditional_use`` claim on expert-tier evidence (the pack's
entries, quoted), and nothing stronger: not that the formula works, not that it suits
this person — those are the practitioner's to judge and a trial's to show.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...contracts import (CandidateClaim, Directness, EvidenceItem, EvidenceQuality,
                          SourceCard, require_declared)
from ...contracts.source_card import canonical_hash
from ..base import artifact, file_of, json_file

__all__ = ["draft_tcm_prescription"]

SKILL_ID = "draft-tcm-prescription"
SKILL_VERSION = "0.1.0"
POPULATION = "the person whose findings are recorded in the intake"
OUTCOME = "syndrome criteria met and the textbook base formula"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _quality() -> EvidenceQuality:
    return EvidenceQuality(
        directness=Directness.DIRECT,
        rationale={"directness": "the textbook entry itself: direct for what the textbook "
                   "says, not evidence about outcomes"},
        assessed_by=SKILL_ID, assessment_tool="corpus-tier-heuristic")


def draft_tcm_prescription(intake: str, *, modifications: str = "",
                           apply_textbook: bool = False, days: int = 7, out_dir: str = "",
                           run_id: str = "") -> Any:
    """``intake``: the intake JSON file; ``modifications``: a JSON list of changes."""
    from ...clinic.pack import load_pack
    from ...clinic.session import assess

    mods = json.loads(modifications) if str(modifications).strip() else []
    if not isinstance(mods, list):
        raise ValueError("modifications is a JSON list of {add|remove|replace|dose: ...}")
    pack = load_pack()
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="bioagent-clinic-"))
    started = _now()
    session = assess(intake, out, pack=pack, modifications=mods,
                     apply_textbook=bool(apply_textbook), days=int(days))
    raw = Path(intake).read_bytes()
    user_card = SourceCard(
        id=f"user.intake.{hashlib.sha256(raw).hexdigest()[:12]}", name="四诊 intake",
        kind="dataset", maintainer="the practitioner", access_method="local_file",
        license_spdx="LicenseRef-user-supplied",
        license_note="a clinical record; it stays with the practitioner",
        integration_mode="native", offline_capable=True,
        snapshot_hash=hashlib.sha256(raw).hexdigest(), snapshot_at=started,
        known_limits=("as complete and accurate as the person who collected it",))
    pack_card = SourceCard(
        id="bioagent.clinic.pack", name="TCMScience clinical knowledge pack",
        kind="guideline", maintainer="TCMScience", version=pack.version,
        access_method="local_file", license_spdx="MIT",
        license_note="a transcription of the textbooks and the Pharmacopoeia it names",
        integration_mode="native", offline_capable=True, snapshot_hash=pack.digest,
        snapshot_at=started,
        known_limits=(pack.review_status, f"{len(pack.syndromes)} syndromes, "
                      f"{len(pack.formulas)} formulas, {len(pack.herbs)} herbs"))

    evidence: list[EvidenceItem] = []
    claims: list[CandidateClaim] = []
    rx = session.prescription
    if session.status in ("draft", "blocked") and rx is not None and rx.formula:
        # The quotes are entries of the pack in its canonical one-line rendering, so each
        # is a verbatim excerpt of the content its receipt names.
        data = json.loads(Path(pack.path).read_text(encoding="utf-8"))
        pack_text = json.dumps(data, ensure_ascii=False)

        def entry(group: str, name: str) -> str:
            return (json.dumps(name, ensure_ascii=False) + ": "
                    + json.dumps(data[group][name], ensure_ascii=False))

        crit, form = entry("syndromes", rx.syndrome), entry("formulas", rx.formula)
        for eid, quote, citation in (
                (f"criteria.{rx.syndrome}", crit, f"{pack.reference.get('criteria', '')}"),
                (f"formula.{rx.formula}", form, f"{pack.reference.get('formulas', '')}")):
            item = EvidenceItem(
                id=eid, design="expert_consensus", quote=quote, citation=citation,
                identifier=f"clinic_pack.json#{eid}", identifier_type="local_artifact",
                source_card_id=pack_card.id, subject=rx.syndrome, population=POPULATION,
                outcome=OUTCOME, quality=_quality(), retrieved_by=SKILL_ID,
                retrieval_run=run_id,
                notes=f"knowledge pack {pack.version}: {pack.review_status}")
            evidence.append(item.located_in(pack_text))
        claims.append(CandidateClaim(
            id="clinic.textbook_formula",
            text=(f"The recorded findings meet the textbook criteria for {rx.syndrome} "
                  f"(治法 {rx.principle}), for which 《方剂学》 gives {rx.formula}"),
            claim_kind="traditional_use", subject=rx.syndrome, predicate="indicated_for",
            object=rx.formula, supports=tuple(e.id for e in evidence),
            asserted_population=POPULATION, supported_population=POPULATION,
            asserted_outcome=OUTCOME, supported_outcome=OUTCOME, direction="unclear",
            hedged=True, confidence=0.5,
            confidence_basis="criteria scored on one intake; the pack is "
                             + pack.review_status,
            falsified_by="a practitioner's examination that finds the criteria are not met, "
                         "or a better-fitting syndrome",
            rationale="a starting point for the practitioner, who decides",
            produced_by=SKILL_ID))
        for claim in claims:
            require_declared(claim)

    summary, _ = json_file("clinic_summary.json", {
        "status": session.status, "out_dir": str(out),
        "red_flags": [f.__dict__ for f in session.red_flags],
        "leading": (session.differentiation.leading.syndrome
                    if session.differentiation and session.differentiation.leading else None),
        "prescription": rx.as_dict() if rx else None,
        "pack": {"version": pack.version, "review_status": pack.review_status},
        "signed": False}, description="the session's status, findings and draft")
    outputs = [summary, file_of("report.md", (out / "report.md").read_text(encoding="utf-8"),
                                media_type="text/markdown",
                                description="the draft for the practitioner to review")]
    limitations = [
        "a draft for a licensed practitioner to review and sign; it is not a prescription",
        f"the knowledge pack is {pack.review_status}",
        "the differentiation is only as good as the intake: findings not recorded are not "
        "scored",
        "agreement with practitioners and clinical benefit are both unvalidated",
    ]
    limitations += list(session.warnings)
    return artifact(
        id=SKILL_ID, run_id=run_id, skill_id=SKILL_ID, skill_version=SKILL_VERSION,
        question="which syndrome do these findings meet, and what does the textbook give "
                 "for it?",
        sources=(user_card, pack_card), evidence=tuple(evidence), claims=tuple(claims),
        outputs=tuple(outputs), limitations=limitations,
        assumptions=("the intake records what was examined, and only that",),
        created_at=_now(),
        provenance={"pipeline": "bioagent.clinic", "status": session.status,
                    "pack_sha256": pack.digest, "session_dir": str(out),
                    "intake_sha256": canonical_hash({"sha256": hashlib.sha256(raw).hexdigest()})})
