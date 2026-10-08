"""Write the governance fixture envelopes used by web/test/governance.test.mjs.

Each fixture is the CONTRACTS §3 envelope around a real result of the TCMScience kernel: the artifact is
ResearchArtifact.document() and the verdict ArtifactVerdict.as_dict(), produced by running the code, not written by
hand. Re-run after a kernel change:

    python3 -I studio/web/test/fixtures/make_governance_fixtures.py studio/web/test/fixtures

Local paths in the output (the temporary state directory, the repository root) are replaced by <tmp> and <repo>, so
the files say nothing about the machine that made them; digests in the artifact refer to the run as it was made.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

from bioagent.config import skills_dir
from bioagent.contracts import CandidateClaim, EvidenceItem
from bioagent.contracts.artifact import ResearchArtifact, validate_artifact
from bioagent.contracts.quality import Consistency, Directness, EvidenceQuality, Precision, RiskOfBias
from bioagent.contracts.receipts import content_sha256
from bioagent.contracts.source_card import SourceCard
from bioagent.governed import run_governed, skill_callables

STARTED = "2026-10-07T12:00:00Z"


def canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(value) -> str:
    return hashlib.sha256(canon(value).encode()).hexdigest()


def envelope(tool, via, args, doc, verdict, *, summary, text, outputs=(), audit_head=None, content_hash=None, kind="skill"):
    sources = doc.get("sources", [])
    licences = [{"asset": s["id"], "licence": s.get("license_spdx") or "unknown",
                 "commercial": s.get("license_spdx") in ("MIT", "Apache-2.0", "CC-BY-4.0", "CC0-1.0"),
                 "note": s.get("license_note", "")} for s in sources]
    refusals = [{"code": v["code"], "message": v["detail"], "remedy": ""} for v in verdict.get("violations", [])]
    for cv in verdict.get("claim_verdicts", []):
        for r in cv.get("reasons", []):
            refusals.append({"code": r["code"], "message": r["detail"], "remedy": ""})
    citations = []
    for n, item in enumerate(doc.get("evidence", []), 1):
        citations.append({"id": f"E{n}", "kind": "source" if item.get("identifier_type") in ("pharmacopoeia", "", None) else item["identifier_type"],
                          "label": item.get("citation") or item["id"], "url": "", "evidence_ref": item["id"]})
    result = {"artifact": doc, "verdict": verdict}
    return {
        "ok": True, "tool": tool, "via": via, "status": "succeeded", "duration_ms": 1240,
        "summary": summary, "text": text, "result": result, "citations": citations,
        "governance": {
            "kind": kind, "released": bool(verdict["states"]["release_authorized"]), "artifact": doc, "verdict": verdict,
            "claims": doc.get("claims", []), "evidence": doc.get("evidence", []), "refusals": refusals,
            "labels": ["INTERNAL"], "licences": licences, "limitations": doc.get("limitations", []),
            "outputs": list(outputs),
        },
        "receipt": {
            "where": "runner", "runtime": "CPython 3.13", "device": "cpu",
            "versions": {"tcmstudio": "0.1.0", "bioagent": "0.2.7", "psh": "0.6.0"},
            "composite_version": doc.get("composite_version_string"), "content_hash": content_hash,
            "audit_head": audit_head, "input_sha256": sha(args), "output_sha256": sha(result), "started_at": STARTED,
        },
        "job": None, "approval": None, "error": None,
    }


def released_safety(out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        args = {"subject": "甘草", "co_administered": ["甘遂"]}
        run = run_governed("assess-tcm-safety", args, skill_dir=skills_dir(None) / "tcm",
                           state_dir=Path(tmp) / "state", output_dir=Path(tmp) / "out")
        doc = run.artifact.document()
        verdict = run.verdict.as_dict()
        outputs = []
        for o in doc.get("outputs", []):
            p = Path(tmp) / "out" / o["path"]
            content = json.loads(p.read_text("utf-8")) if p.exists() and o["media_type"] == "application/json" else None
            outputs.append({**o, "content": content})
        env = envelope("tcm_safety_report", "skill.assess-tcm-safety", args, doc, verdict,
                       summary="甘草 + 甘遂：记载为十八反配伍禁忌；4 条安全性记录（记载，非临床安全性结论）",
                       text="Recorded safety information for 甘草 with 甘遂 (seed corpus; absence of a record is not evidence of safety). [E1] [E2]",
                       outputs=outputs, audit_head=run.audit_head, content_hash=run.content_hash)
    (out / "envelope_safety_released.json").write_text(json.dumps(env, ensure_ascii=False, indent=1), "utf-8")


def unreleased_network(out: Path) -> None:
    fn = skill_callables()["analyze-tcm-network-pharmacology"]
    artifact = fn(formula_name="桂枝汤")
    doc = artifact.document()
    verdict = validate_artifact(artifact).as_dict()  # no output root, no attestor: consistent, not verified
    env = envelope("tcm_network_hypothesis", "skill.analyze-tcm-network-pharmacology", {"formula_name": "桂枝汤"}, doc, verdict,
                   summary="桂枝汤：网络药理学假说（种子语料；预测 ≠ 实测）",
                   text="Network for 桂枝汤 from the seed corpus. Every target edge is a recorded relation or an inference; none is a binding measurement.")
    (out / "envelope_network_unreleased.json").write_text(json.dumps(env, ensure_ascii=False, indent=1), "utf-8")


def refused_claims(out: Path) -> None:
    source = SourceCard(
        id="demo.literature", name="Demonstration literature snapshot", kind="literature_index",
        maintainer="TCMScience tests", version="1", home_url="https://europepmc.org", endpoint="",
        access_method="literature_index", allowed_hosts=("www.ebi.ac.uk",), license_spdx="CC-BY-4.0", license_note="",
        integration_mode="native", snapshot_hash="a" * 64, snapshot_at="2026-10-01T00:00:00Z",
        operation_hashes={}, known_limits=("a fixture for the Studio tests",), offline_capable=True, notes="")
    quality = EvidenceQuality(risk_of_bias=RiskOfBias.NOT_ASSESSED, directness=Directness.EXTRAPOLATED,
                              precision=Precision.NOT_ASSESSED, consistency=Consistency.SINGLE_STUDY, rationale={}, assessed_by="fixture", assessment_tool="manual")
    # quote receipts: the quote is located in the content its hash names, so CLM010 does not fire
    bench_text = "In this study puerarin reduced TNF-alpha secretion in RAW264.7 cells after LPS stimulation."
    dock_text = "Result: puerarin docks to TNF with a score of -8.1 kcal/mol (AutoDock Vina)."
    bench = EvidenceItem(
        id="ev.invitro", design="in_vitro", quote="puerarin reduced TNF-alpha secretion in RAW264.7 cells",
        citation="Doe J. et al. 2020. J Ethnopharmacol.", title="Puerarin in macrophages", identifier="12345678",
        identifier_type="pmid", source_card_id="demo.literature", content_hash=content_sha256(bench_text), quote_verified=True,
        quote_offset=bench_text.index("puerarin reduced"),
        quality=quality, subject="葛根", population="RAW264.7 cells", condition="inflammation", comparator="vehicle",
        outcome="TNF-alpha", effect="decrease", sample_size=0, year=2020, retracted="not_retracted",
        retrieved_by="fixture", retrieval_run="", retrieved_at=0.0, conflicts_with=(), notes="")
    dock = EvidenceItem(
        id="ev.docking", design="docking", quote="puerarin docks to TNF with a score of -8.1 kcal/mol",
        citation="Docking run, this project", title="", identifier="", identifier_type="local_artifact",
        source_card_id="demo.literature", content_hash=content_sha256(dock_text), quote_verified=True,
        quote_offset=dock_text.index("puerarin docks"), quality=None,
        subject="葛根", population="", condition="", comparator="", outcome="binding", effect="", sample_size=0,
        year=2026, retracted="unverified", retrieved_by="fixture", retrieval_run="", retrieved_at=0.0,
        conflicts_with=(), notes="")
    claims = (
        CandidateClaim(id="claim.efficacy", text="葛根 reduces inflammation in patients.", claim_kind="efficacy",
                       subject="葛根", predicate="reduces", object="inflammation", supports=("ev.invitro",),
                       asserted_population="patients", asserted_outcome="inflammation",
                       supported_population="RAW264.7 cells", supported_outcome="TNF-alpha",
                       direction="decrease", magnitude="", declared_extrapolations={}, validated_extrapolations={},
                       confidence=0.0, confidence_basis="", hedged=False, normative=False, rationale="",
                       falsified_by="a randomized trial with no effect on inflammation", produced_by="fixture"),
        CandidateClaim(id="claim.prediction", text="葛根 is effective because puerarin binds TNF.", claim_kind="efficacy",
                       subject="葛根", predicate="treats", object="inflammation", supports=("ev.docking",),
                       asserted_population="", asserted_outcome="", supported_population="", supported_outcome="",
                       direction="decrease", magnitude="", declared_extrapolations={}, validated_extrapolations={},
                       confidence=0.0, confidence_basis="", hedged=False, normative=False, rationale="",
                       falsified_by="", produced_by="fixture"),
        CandidateClaim(id="claim.hypothesis", text="Puerarin may bind TNF (a docking prediction).", claim_kind="mechanism_hypothesis",
                       subject="puerarin", predicate="binds", object="TNF", supports=("ev.docking",),
                       asserted_population="", asserted_outcome="", supported_population="", supported_outcome="",
                       direction="unclear", magnitude="", declared_extrapolations={}, validated_extrapolations={},
                       confidence=0.0, confidence_basis="", hedged=True, normative=False, rationale="",
                       falsified_by="a binding assay showing no affinity", produced_by="fixture"),
    )
    artifact = ResearchArtifact(
        id="fixture.refused", run_id="fixture", skill_id="fixture", skill_version="0",
        composite_version={"runtime": "psh-0.6.0+bioagent-0.2.7", "skill": "fixture@0", "source": "a" * 64, "benchmark": "unversioned"},
        question="Does 葛根 reduce inflammation?", created_at=STARTED, sources=(source,), evidence=(bench, dock), claims=claims,
        outputs=(), limitations=("fixture: two evidence items only",), assumptions=(), policy_id="", audit_head="",
        provenance={}, status="draft", produced_by="fixture", notes="")
    doc = artifact.document()
    verdict = validate_artifact(artifact).as_dict()
    env = envelope("call_tool", "fixture.refused", {"tool": "fixture.refused", "arguments": {}}, doc, verdict,
                   summary="3 条主张：1 条允许，2 条已拒绝（CLM005、CLM004）",
                   text="Two claims were refused by the kernel; one mechanism hypothesis is allowed.", kind="skill")
    (out / "envelope_claims_refused.json").write_text(json.dumps(env, ensure_ascii=False, indent=1), "utf-8")


def scrub_paths(out: Path) -> None:
    repo = str(Path(__file__).resolve().parents[4])
    for f in out.glob("envelope_*.json"):
        text = f.read_text("utf-8").replace(repo, "<repo>")
        text = re.sub(r"(/private)?/(var/folders/[^\"]+?|tmp)/tmp[a-z0-9_]+", "<tmp>", text)
        f.write_text(text, "utf-8")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    released_safety(out)
    unreleased_network(out)
    refused_claims(out)
    scrub_paths(out)


if __name__ == "__main__":
    main()
