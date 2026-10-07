"""Write the design-gallery fixtures (studio/web/dev/fixtures/*.json) from real TCMScience runs.

Every governance object in these files was produced by running the kernel: governed skills through
bioagent.governed.run_governed (with a durable PSH state directory, so the run is attested), a skill artifact
validated without an output root (so it is "consistent, not verified"), claims judged by validate_artifact, and
native tools called directly. Only the envelope wrapping (CONTRACTS §3) and the receipt's placement fields are
written here; the outcomes the router decides in the page (needs approval, denied, unavailable, network off) are
built the way core/router.js builds them.

    python3 -I studio/web/dev/fixtures/make_fixtures.py studio/web/dev/fixtures
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

from bioagent.config import skills_dir
from bioagent.contracts import CandidateClaim, EvidenceItem
from bioagent.contracts.artifact import ResearchArtifact, validate_artifact
from bioagent.contracts.quality import Consistency, Directness, EvidenceQuality, Precision, RiskOfBias
from bioagent.contracts.receipts import content_sha256
from bioagent.contracts.source_card import SourceCard
from bioagent.governed import run_governed, skill_callables
from bioagent.tools import tool

STARTED = "2026-10-07T09:41:12Z"
VERSIONS = {"tcmstudio": "0.1.0", "bioagent": "0.2.7", "psh": "0.6.0"}
BROWSER = {"where": "browser", "runtime": "pyodide-314.0.7 / CPython 3.14", "device": "cpu"}
RUNNER = {"where": "runner", "runtime": "CPython 3.13 · tcmstudio 0.1.0", "device": "cpu"}
EMPTY_GOV = {"released": None, "artifact": None, "verdict": None, "claims": [], "evidence": [], "refusals": [], "labels": [],
             "licences": [], "limitations": [], "outputs": []}


def canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(value) -> str:
    return hashlib.sha256(canon(value).encode()).hexdigest()


def governed_envelope(tool_name, via, args, doc, verdict, *, summary, text, ms, place, outputs=(), audit_head=None, content_hash=None):
    sources = doc.get("sources", [])
    licences = [{"asset": s["id"], "licence": s.get("license_spdx") or "", "commercial": bool(s.get("license_spdx")),
                 "note": s.get("license_note", "")} for s in sources]
    refusals = [{"code": v["code"], "message": v["detail"], "remedy": ""} for v in verdict.get("violations", [])]
    for cv in verdict.get("claim_verdicts", []):
        for r in cv.get("reasons", []):
            refusals.append({"code": r["code"], "message": r["detail"], "remedy": ""})
    citations = []
    for n, item in enumerate(doc.get("evidence", []), 1):
        kind = item.get("identifier_type") or "source"
        citations.append({"id": f"E{n}", "kind": kind if kind in ("pmid", "doi", "nct") else ("classical" if kind == "classical_passage" else "source"),
                          "label": item.get("citation") or item["id"], "url": "", "evidence_ref": item["id"]})
    result = {"artifact": doc, "verdict": verdict}
    return {
        "ok": True, "tool": tool_name, "via": via, "status": "succeeded", "duration_ms": ms,
        "summary": summary, "text": text, "result": result, "citations": citations,
        "governance": {
            "kind": "skill", "released": bool(verdict["states"]["release_authorized"]), "artifact": doc, "verdict": verdict,
            "claims": doc.get("claims", []), "evidence": doc.get("evidence", []), "refusals": refusals,
            "labels": ["INTERNAL"], "licences": licences, "limitations": doc.get("limitations", []), "outputs": list(outputs),
        },
        "receipt": {**place, "versions": VERSIONS, "composite_version": doc.get("composite_version_string"),
                    "content_hash": content_hash, "audit_head": audit_head, "input_sha256": sha(args),
                    "output_sha256": sha(result), "started_at": STARTED, "durable": place["where"] == "runner"},
        "job": None, "approval": None, "error": None,
    }


def native_envelope(tool_name, via, args, result, *, summary, text, ms, place, limitations=()):
    return {
        "ok": True, "tool": tool_name, "via": via, "status": "succeeded", "duration_ms": ms, "summary": summary,
        "text": text, "result": result, "citations": [],
        "governance": {**EMPTY_GOV, "kind": "native", "limitations": list(limitations)},
        "receipt": {**place, "versions": VERSIONS, "composite_version": None, "content_hash": None, "audit_head": None,
                    "input_sha256": sha(args), "output_sha256": sha(result), "started_at": STARTED},
        "job": None, "approval": None, "error": None,
    }


def router_envelope(tool_name, via, args, *, status, summary, text, error=None, approval=None, kind="native", place=BROWSER):
    return {
        "ok": False, "tool": tool_name, "via": via, "status": status, "duration_ms": 0, "summary": summary, "text": text,
        "result": None, "citations": [], "governance": {**EMPTY_GOV, "kind": kind},
        "receipt": {"where": place["where"], "runtime": "studio-router", "device": place.get("device", "cpu"), "versions": {}, "composite_version": None,
                    "content_hash": None, "audit_head": None, "input_sha256": sha(args), "output_sha256": None,
                    "started_at": STARTED, "decided_by": "router"},
        "job": None, "approval": approval, "error": error,
    }


def governed(skill, args, out: Path):
    t0 = time.perf_counter()
    run = run_governed(skill, args, skill_dir=skills_dir(None) / "tcm", state_dir=out / "state", output_dir=out / "out")
    ms = round((time.perf_counter() - t0) * 1000)
    doc = run.artifact.document()
    outputs = []
    for o in doc.get("outputs", []):
        p = out / "out" / o["path"]
        content = json.loads(p.read_text("utf-8")) if p.exists() and o.get("media_type") == "application/json" else None
        outputs.append({**o, "content": content})
    return run, doc, run.verdict.as_dict(), outputs, ms


def write(out: Path, name: str, value) -> None:
    (out / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", "utf-8")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # 甘草 + 甘遂: an 十八反 pair, reported as a record (governed, attested on the runner)
        args = {"subject": "甘草", "co_administered": ["甘遂"]}
        run, doc, verdict, outputs, ms = governed("assess-tcm-safety", args, tmp / "safety")
        write(out, "envelope_safety", governed_envelope(
            "tcm_safety_report", "skill.assess-tcm-safety", args, doc, verdict, ms=ms, place=RUNNER,
            summary="甘草 + 甘遂：记载为十八反配伍禁忌；4 条安全性记录（记载，非临床安全性结论）",
            text=("Recorded safety information for 甘草 co-administered with 甘遂 (seed corpus). The pair is recorded as an "
                  "十八反 incompatibility [E1]; 4 safety records concern 甘草, 3 at high or critical severity [E2]. These are "
                  "corpus records at expert-experience level, not clinical safety data. Absence of a record is not evidence of "
                  "safety. Release authorized by the kernel (6/6)."),
            outputs=outputs, audit_head=run.audit_head, content_hash=run.content_hash))

        # 姜, 白芍: ambiguity preserved, not resolved (governed, in the browser)
        args = {"names": ["姜", "白芍"]}
        run, doc, verdict, outputs, ms = governed("normalize-tcm-entities", args, tmp / "norm")
        write(out, "envelope_normalize", governed_envelope(
            "tcm_normalize", "skill.normalize-tcm-entities", args, doc, verdict, ms=ms, place=BROWSER,
            summary="姜：有歧义（生姜 / 干姜），未自动选择；白芍：唯一匹配",
            text=("姜 is ambiguous in the seed corpus: it may be 生姜 (fresh ginger) or 干姜 (dried ginger); both candidates are "
                  "kept and none is chosen. 白芍 resolves to one entity. Name normalisation asserts nothing about effects."),
            outputs=outputs, audit_head=run.audit_head, content_hash=run.content_hash))

        # 桂枝汤 network: every edge predicted or recorded; attested governed run
        args = {"formula_name": "桂枝汤"}
        run, doc, verdict, outputs, ms = governed("analyze-tcm-network-pharmacology", args, tmp / "net")
        write(out, "envelope_network", governed_envelope(
            "tcm_network_hypothesis", "skill.analyze-tcm-network-pharmacology", args, doc, verdict, ms=ms, place=RUNNER,
            summary="桂枝汤：网络药理学假说（种子语料）；预测 ≠ 实测",
            text=("Herb–target network for 桂枝汤 from the seed corpus. Every target edge is a corpus-recorded relation or a "
                  "network inference; none is a binding measurement. It supports a mechanism hypothesis at most."),
            outputs=outputs, audit_head=run.audit_head, content_hash=run.content_hash))

        # the same skill, validated without an output root or an attestor: consistent, not verified
        artifact = skill_callables()["analyze-tcm-network-pharmacology"](formula_name="桂枝汤")
        doc = artifact.document()
        verdict = validate_artifact(artifact).as_dict()
        write(out, "envelope_network_unattested", governed_envelope(
            "tcm_network_hypothesis", "skill.analyze-tcm-network-pharmacology", args, doc, verdict, ms=412, place=BROWSER,
            summary="桂枝汤：网络药理学假说（未经审计链证明）",
            text="Network for 桂枝汤; the outputs were not checked and the run is not attested by the kernel's audit chain."))

    # claims judged by the kernel: an efficacy claim on bench evidence (CLM005), a prediction stated as fact (CLM004)
    write(out, "envelope_claims", refused_claims())

    # native tools, called directly
    fn = tool("tcm_compatibility").fn
    args = {"herbs": ["甘草", "甘遂"]}
    t0 = time.perf_counter(); res = fn(**args); ms = max(1, round((time.perf_counter() - t0) * 1000))
    write(out, "envelope_compatibility", native_envelope(
        "tcm_compatibility", "native.tcm_compatibility", args, res, ms=ms + 37, place=BROWSER,
        summary="甘草 + 甘遂：1 条配伍冲突（十八反，记载）",
        text=json.dumps(res, ensure_ascii=False),
        limitations=["recorded relations from the seed corpus; no record is not evidence of compatibility"]))

    fn = tool("tcm_herb").fn
    args = {"name": "黄芪"}
    t0 = time.perf_counter(); res = fn(**args); ms = max(1, round((time.perf_counter() - t0) * 1000))
    write(out, "envelope_herb", native_envelope("tcm_herb", "native.tcm_herb", args, res, ms=ms + 21, place=BROWSER,
                                                summary="黄芪 Astragali Radix：甘，微温；归脾、肺经", text=json.dumps(res, ensure_ascii=False)))

    fn = tool("reverse_complement").fn
    args = {"sequence": "ATGCGTACGTTAGC"}
    res = fn(**args)
    write(out, "envelope_revcomp", native_envelope("call_tool", "native.reverse_complement", {"tool": "native.reverse_complement", "arguments": args},
                                                   res, ms=3, place=BROWSER, summary=f"反向互补：{res.get('reverse_complement', '')}",
                                                   text=json.dumps(res, ensure_ascii=False)))

    fn = tool("egfr_ckd_epi_2021").fn
    args = {"creatinine_mg_dl": 1.0, "age_years": 50, "sex": "female"}
    res = fn(**args)
    write(out, "envelope_egfr", native_envelope("call_tool", "native.egfr_ckd_epi_2021", {"tool": "native.egfr_ckd_epi_2021", "arguments": args},
                                                res, ms=2, place=BROWSER, summary=f"eGFR {res['egfr_ml_min_1_73m2']} mL/min/1.73m² · {res['kdigo_stage']}",
                                                text=json.dumps(res, ensure_ascii=False)))

    # outcomes decided in the page by the router (core/router.js shapes)
    write(out, "envelope_needs_runner", router_envelope(
        "literature_search", "connector.europepmc.search", {"query": "Puerariae Lobatae Radix inflammation"}, status="failed", kind="connector",
        summary="literature_search 需要本机 Runner，当前未连接。浏览器内计算仍可用；连接 Runner 后可运行。",
        text="Not run: literature_search needs the local runner, which is not connected. Say what it would need; do not substitute an approximation.",
        error={"type": "unavailable", "message": "literature_search 需要本机 Runner，当前未连接。", "hint": "needs_runner"}))
    write(out, "envelope_network_off", router_envelope(
        "connector_call", "connector.chembl.molecule", {"connector": "chembl", "operation": "molecule", "arguments": {"q": "puerarin"}}, status="failed", kind="connector", place=RUNNER,
        summary="本项目未开启联网，connector_call 需要访问网络。可在项目设置中开启联网后重试。",
        text="Not run: web access is off for this project and this tool reaches the network.",
        error={"type": "network_off", "message": "本项目未开启联网，connector_call 需要访问网络。", "hint": "turn on web access for this project"}))
    write(out, "envelope_denied", router_envelope(
        "network_pharmacology_run", "job.research.run", {"formula": "葛根芩连汤", "disease": "2 型糖尿病"}, status="refused", kind="job", place=RUNNER,
        summary="未获批准：network_pharmacology_run 没有运行。",
        text="Not run: the user declined this call (job, first_runner_call).",
        error={"type": "refused", "message": "未获批准：network_pharmacology_run 没有运行。", "hint": "the user declined"},
        approval={"reason": "job", "what": "网络药理学研究闭环 (job.research.run)", "hosts": [], "decision": "deny"}))

    job_env = router_envelope("run_pipeline", "job.pipeline.dock", {"pipeline": "dock", "arguments": {"ligand": "puerarin", "receptor": "upload:u_7f3a"}},
                              status="job_submitted", kind="job", place={**RUNNER, "device": "cuda:0"}, summary="", text="")
    job_env.update({"ok": True, "summary": "已在本机 Runner 上提交对接任务（dock）", "text": "Job j_01HF8T submitted (pipeline.dock). Use job_status to follow it; its outputs are pending until collected and verified.",
                    "job": {"id": "j_01HF8T", "kind": "pipeline.dock", "state": "queued"}, "error": None, "duration_ms": 84})
    job_env["receipt"].update({"runtime": "CPython 3.13 · tcmstudio 0.1.0"})
    del job_env["receipt"]["decided_by"]
    write(out, "envelope_job", job_env)

    write(out, "jobs", {
        "running": {"id": "j_01HF8T", "kind": "pipeline.dock", "state": "running", "params": {"ligand": "puerarin", "receptor": "upload:u_7f3a"},
                    "project_id": "p_demo", "created_at": "2026-10-07T09:44:02Z", "started_at": "2026-10-07T09:44:05Z", "finished_at": None,
                    "progress": {"fraction": 0.42, "message": "Vina: exhaustiveness 8 · pose 4/10"}, "device": "cuda:0", "outcome": None, "artefacts": [],
                    "files_url": "/api/jobs/j_01HF8T/files"},
        "succeeded": {"id": "j_01HF7Q", "kind": "pipeline.admet", "state": "succeeded", "params": {"smiles": ["OC1=CC=C(C=C1)C1=COC2=C(C1=O)C=CC(O)=C2"]},
                      "project_id": "p_demo", "created_at": "2026-10-07T09:20:11Z", "started_at": "2026-10-07T09:20:12Z", "finished_at": "2026-10-07T09:21:40Z",
                      "progress": None, "device": "cpu", "outcome": {"status": "succeeded"},
                      "artefacts": [{"name": "admet.json", "sha256": sha({"admet": 1}), "bytes": 4210}, {"name": "report.html", "sha256": sha({"report": 1}), "bytes": 18234}],
                      "files_url": "/api/jobs/j_01HF7Q/files"},
        "failed": {"id": "j_01HF6M", "kind": "pipeline.fold", "state": "failed", "params": {"sequence": "MKTAYIAKQR…"}, "project_id": "p_demo",
                   "created_at": "2026-10-07T08:02:00Z", "started_at": "2026-10-07T08:02:01Z", "finished_at": "2026-10-07T08:02:09Z", "progress": None,
                   "device": "cpu", "outcome": {"status": "failed", "error": "Boltz-2 is not installed on this runner; nothing approximate was substituted.",
                                                 "problems": ["missing engine: boltz"]}, "artefacts": [], "files_url": "/api/jobs/j_01HF6M/files"},
        "queued": {"id": "j_01HF9A", "kind": "research.run", "state": "queued", "params": {"formula": "葛根芩连汤"}, "project_id": "p_demo",
                   "created_at": "2026-10-07T09:50:00Z", "started_at": None, "finished_at": None, "progress": None, "device": "cpu", "outcome": None,
                   "artefacts": [], "files_url": "/api/jobs/j_01HF9A/files"},
    })

    write(out, "approvals", [
        {"callId": "c1", "tool": "literature_search", "entry": "connector.europepmc.search", "reason": "network", "reasons": ["network", "first_runner_call"],
         "what": "文献检索 (connector.europepmc.search)", "hosts": ["www.ebi.ac.uk"], "args": {"query": "葛根素 炎症", "limit": 10}},
        {"callId": "c2", "tool": "network_pharmacology_run", "entry": "job.research.run", "reason": "job", "reasons": ["job"],
         "what": "网络药理学研究闭环 (job.research.run)", "hosts": [], "args": {"formula": "葛根芩连汤", "disease": "2 型糖尿病"}},
        {"callId": "c3", "tool": "call_tool", "entry": "connector.colabfold.msa", "reason": "remote_upload", "reasons": ["network", "remote_upload"],
         "what": "ColabFold MSA (connector.colabfold.msa)", "hosts": ["api.colabfold.com"], "args": {"tool": "connector.colabfold.msa", "arguments": {"sequence": "MKTAYIAKQRQISFVKSHFSRQ", "allow_remote": True}}},
    ])
    print("wrote", sorted(p.name for p in out.glob("*.json")))


def refused_claims():
    source = SourceCard(
        id="europepmc.snapshot.2026-10", name="Europe PMC snapshot (demonstration)", kind="literature_index",
        maintainer="TCMScience", version="2026.10", home_url="https://europepmc.org", endpoint="",
        access_method="literature_index", allowed_hosts=("www.ebi.ac.uk",), license_spdx="CC-BY-4.0", license_note="",
        integration_mode="native", snapshot_hash=hashlib.sha256(b"europepmc-2026-10").hexdigest(), snapshot_at="2026-10-01T00:00:00Z",
        operation_hashes={}, known_limits=("abstracts only", "retraction status is checked at snapshot time"), offline_capable=True, notes="")
    quality = EvidenceQuality(risk_of_bias=RiskOfBias.NOT_ASSESSED, directness=Directness.EXTRAPOLATED,
                              precision=Precision.NOT_ASSESSED, consistency=Consistency.SINGLE_STUDY, rationale={}, assessed_by="reviewer", assessment_tool="manual")
    bench_text = "In this study puerarin reduced TNF-alpha secretion in RAW264.7 cells after LPS stimulation."
    dock_text = "Result: puerarin docks to TNF with a score of -8.1 kcal/mol (AutoDock Vina)."
    bench = EvidenceItem(
        id="ev.invitro", design="in_vitro", quote="puerarin reduced TNF-alpha secretion in RAW264.7 cells",
        citation="Zhang L, et al. Puerarin attenuates LPS-induced inflammation in macrophages. J Ethnopharmacol. 2020.",
        title="Puerarin in macrophages", identifier="32112345", identifier_type="pmid", source_card_id=source.id,
        content_hash=content_sha256(bench_text), quote_verified=True, quote_offset=bench_text.index("puerarin reduced"),
        quality=quality, subject="葛根素 puerarin", population="RAW264.7 cells", condition="inflammation", comparator="vehicle",
        outcome="TNF-alpha", effect="decrease", sample_size=0, year=2020, retracted="not_retracted",
        retrieved_by="literature_search", retrieval_run="", retrieved_at=0.0, conflicts_with=(), notes="")
    dock = EvidenceItem(
        id="ev.docking", design="docking", quote="puerarin docks to TNF with a score of -8.1 kcal/mol",
        citation="Docking run j_01HF8T, this project", title="", identifier="", identifier_type="local_artifact",
        source_card_id=source.id, content_hash=content_sha256(dock_text), quote_verified=True,
        quote_offset=dock_text.index("puerarin docks"), quality=None,
        subject="葛根素 puerarin", population="", condition="", comparator="", outcome="binding", effect="", sample_size=0,
        year=2026, retracted="unverified", retrieved_by="run_pipeline", retrieval_run="j_01HF8T", retrieved_at=0.0, conflicts_with=(), notes="")
    claims = (
        CandidateClaim(id="claim.efficacy", text="葛根可减轻患者的炎症反应。", claim_kind="efficacy",
                       subject="葛根", predicate="reduces", object="inflammation", supports=("ev.invitro",),
                       asserted_population="patients", asserted_outcome="inflammation",
                       supported_population="RAW264.7 cells", supported_outcome="TNF-alpha",
                       direction="decrease", magnitude="", declared_extrapolations={}, validated_extrapolations={},
                       confidence=0.0, confidence_basis="", hedged=False, normative=False, rationale="",
                       falsified_by="一项随机对照试验显示对炎症指标无影响", produced_by="model"),
        CandidateClaim(id="claim.prediction", text="葛根有效，因为葛根素与 TNF 结合。", claim_kind="efficacy",
                       subject="葛根", predicate="treats", object="inflammation", supports=("ev.docking",),
                       asserted_population="", asserted_outcome="", supported_population="", supported_outcome="",
                       direction="decrease", magnitude="", declared_extrapolations={}, validated_extrapolations={},
                       confidence=0.0, confidence_basis="", hedged=False, normative=False, rationale="",
                       falsified_by="", produced_by="model"),
        CandidateClaim(id="claim.hypothesis", text="葛根素可能与 TNF 结合（分子对接预测）。", claim_kind="mechanism_hypothesis",
                       subject="葛根素", predicate="binds", object="TNF", supports=("ev.docking",),
                       asserted_population="", asserted_outcome="", supported_population="", supported_outcome="",
                       direction="unclear", magnitude="", declared_extrapolations={"population:cells → humans": "in vitro and in silico only; not tested in people"}, validated_extrapolations={},
                       confidence=0.4, confidence_basis="one docking score and one cell study; no binding assay", hedged=True, normative=False, rationale="",
                       falsified_by="结合实验（SPR / ITC）显示无亲和力", produced_by="model"),
    )
    artifact = ResearchArtifact(
        id="review.puerarin", run_id="r_demo", skill_id="review-claims", skill_version="0.1.0",
        composite_version={"runtime": "psh-0.6.0+bioagent-0.2.7", "skill": "review-claims@0.1.0", "source": source.snapshot_hash, "benchmark": "unversioned"},
        question="葛根能否减轻炎症？", created_at=STARTED, sources=(source,), evidence=(bench, dock), claims=claims,
        outputs=(), limitations=("two evidence items only: one cell study and one docking run", "no clinical evidence was retrieved"),
        assumptions=("葛根素 is taken as the active constituent of 葛根",), policy_id="", audit_head="",
        provenance={}, status="draft", produced_by="model", notes="")
    doc = artifact.document()
    verdict = validate_artifact(artifact).as_dict()
    args = {"tool": "study.review_claims", "arguments": {"question": "葛根能否减轻炎症？"}}
    return governed_envelope("call_tool", "study.review_claims", args, doc, verdict, ms=318, place=BROWSER,
                             summary="3 条主张：1 条允许（机制假说），2 条已拒绝（CLM005、CLM004）",
                             text="Two claims were refused by the kernel (CLM005: an efficacy claim rests on a preclinical study; CLM004: a clinical claim rests on a docking prediction). One mechanism hypothesis is allowed, with a declared extrapolation.")


if __name__ == "__main__":
    main()
