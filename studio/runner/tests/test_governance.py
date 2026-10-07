"""Governance extraction: what the kernel decided, read from real artifacts and rulings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcmstudio import governance as gv


@pytest.fixture(scope="module")
def released_run(tmp_path_factory):
    from bioagent.config import skills_dir
    from bioagent.governed import run_governed
    tmp = tmp_path_factory.mktemp("gov")
    return run_governed("assess-tcm-safety", {"subject": "甘草", "co_administered": ["甘遂"]},
                        skill_dir=Path(skills_dir()) / "tcm", state_dir=tmp / "state",
                        output_dir=tmp / "out")


def refused_artifact():
    """Claims the kernel refuses: efficacy on in-vitro evidence (CLM005) and efficacy on a
    docking prediction (CLM004 — a prediction stated as fact)."""
    from bioagent.contracts import CandidateClaim, EvidenceItem
    from bioagent.contracts.artifact import ResearchArtifact
    from bioagent.contracts.receipts import content_sha256
    from bioagent.contracts.source_card import SourceCard

    source = SourceCard(id="demo.lit", name="Demo", kind="literature_index",
                        access_method="literature_index", allowed_hosts=("www.ebi.ac.uk",),
                        license_spdx="CC-BY-4.0",
                        snapshot_hash="a" * 64, snapshot_at="2026-10-01T00:00:00Z",
                        known_limits=("a test",), offline_capable=True)
    bench_text = "puerarin reduced TNF-alpha secretion in RAW264.7 cells"
    dock_text = "puerarin docks to TNF with a score of -8.1 kcal/mol"
    bench = EvidenceItem(id="ev.invitro", design="in_vitro", quote=bench_text,
                         citation="Doe 2020", identifier="12345678", identifier_type="pmid",
                         source_card_id="demo.lit", content_hash=content_sha256(bench_text),
                         quote_verified=True, quote_offset=0)
    dock = EvidenceItem(id="ev.dock", design="docking", quote=dock_text, citation="Docking run",
                        identifier_type="local_artifact", source_card_id="demo.lit",
                        content_hash=content_sha256(dock_text), quote_verified=True,
                        quote_offset=0)
    claims = (CandidateClaim(id="c.eff", text="葛根 reduces inflammation in patients.",
                             claim_kind="efficacy", subject="葛根", supports=("ev.invitro",)),
              CandidateClaim(id="c.pred", text="葛根 is effective because puerarin binds TNF.",
                             claim_kind="efficacy", subject="葛根", supports=("ev.dock",)),
              CandidateClaim(id="c.hyp", text="Puerarin may bind TNF (a docking prediction).",
                             claim_kind="mechanism_hypothesis", subject="puerarin",
                             supports=("ev.dock",), hedged=True))
    return ResearchArtifact(
        id="t.refused", run_id="t", skill_id="t", skill_version="0",
        composite_version={"runtime": "r", "skill": "t@0", "source": "a" * 64,
                           "benchmark": "unversioned"},
        question="Does 葛根 reduce inflammation?", sources=(source,), evidence=(bench, dock),
        claims=claims, limitations=("two evidence items only",))


def test_refusals_from_a_verdict_carry_codes_and_remedies():
    from bioagent.contracts.artifact import validate_artifact
    art = refused_artifact()
    verdict = validate_artifact(art).as_dict()
    refusals = gv.from_verdict_refusals(verdict, art.document()["claims"])
    codes = {r["code"] for r in refusals}
    assert {"CLM005", "CLM004"} <= codes or {"CLM005"} <= codes, refusals
    for r in refusals:
        assert r["message"] and r["remedy"], r
        if r["code"].startswith("CLM"):
            assert r["claim_id"] in ("c.eff", "c.pred") and r["claim"]
    assert not any(r.get("claim_id") == "c.hyp" for r in refusals)
    clm005 = next(r for r in refusals if r["code"] == "CLM005")
    assert "tcm_applicability" in clm005["remedy"]


def test_governed_run_governance(released_run):
    g = gv.from_governed_run(released_run, limits=[gv.LIMIT_NO_RECORD])
    assert g["kind"] == "skill" and g["released"] is True
    assert g["limitations"][0] == gv.LIMIT_NO_RECORD and len(g["limitations"]) > 1
    assert g["verdict"]["states"]["release_authorized"] is True
    assert g["claims"] and g["evidence"] and g["refusals"] == []
    out = g["outputs"][0]
    data = (Path(released_run.output_dir) / "safety.json").read_bytes()
    import hashlib
    assert out["path"] == "safety.json" and out["sha256"] == hashlib.sha256(data).hexdigest()
    assert out["bytes"] == len(data) and out["content"] == json.loads(data)
    assert g["licences"] == [{"asset": "tcmscience.tcm.seed", "licence": "MIT", "class": "open",
                              "commercial": True, "note": ""}]
    json.dumps(g, ensure_ascii=False, allow_nan=False)


def test_policy_denial_becomes_a_refusal():
    from bioagent.providers.public_apis import BY_KEY
    from bioagent.psh.arguments import arguments_for
    from bioagent.psh.assembly import default_runtime
    from bioagent.runtime.agentspec import AgentSpec
    rt = default_runtime(catalogue=False, skills=False, tooluniverse=False)
    kwargs = arguments_for({"operation": "entry", "accession": "P04637"}, source=BY_KEY["uniprot"],
                           component_id="public.connector.uniprot")
    res = rt.invoke("public.connector.uniprot",
                    spec=AgentSpec(name="t", permission_profile="offline-analysis"), **kwargs)
    g = gv.from_call_result(res, kind="connector", limits=[gv.LIMIT_LIVE])
    assert g["policy"]["allowed"] is False
    deny = [r for r in g["refusals"] if r["code"] == "perm.network.denied"]
    assert deny and "web access" in deny[0]["remedy"]
    assert g["limitations"] == [gv.LIMIT_LIVE]


def test_outputs_from(tmp_path):
    (tmp_path / "a.json").write_text('{"x": 1}', encoding="utf-8")
    (tmp_path / "b.md").write_text("# 报告", encoding="utf-8")
    (tmp_path / "c.html").write_text("<p>x</p>", encoding="utf-8")
    (tmp_path / "big.json").write_text(json.dumps(["x" * 1000] * 600), encoding="utf-8")
    out = {o["path"]: o for o in gv.outputs_from(tmp_path, [
        {"path": "a.json", "media_type": "application/json", "sha256": "wrong"}, "b.md",
        "c.html", "big.json", "missing.json"])}
    assert out["a.json"]["content"] == {"x": 1} and out["a.json"]["hash_mismatch"] is True
    assert out["b.md"]["content"] == "# 报告" and out["b.md"]["media_type"] == "text/markdown"
    assert out["c.html"]["content"] is None
    assert out["big.json"]["content"] is None and out["big.json"]["bytes"] > 512 * 1024
    assert out["missing.json"]["missing"] is True


def test_advisory_labels():
    labels, detail = gv.advisory_labels("患者张三，身份证 110101199003071234，手机 13800138000")
    assert labels and labels[0] in ("PHI", "SECRET")
    assert detail["advisory"] is True and detail["permits"]["LOCAL_COMPUTE"] is True
    assert detail["permits"]["PUBLIC_REMOTE"] is False
    plain, _ = gv.advisory_labels({"gc_fraction": 0.5})
    assert plain == ["INTERNAL"]


def test_licence_entries_are_conservative():
    assert gv.licence_entry("x", "MIT")["commercial"] is True
    nc = gv.licence_entry("y", "CC BY-NC-ND 4.0")
    assert nc["commercial"] is False and nc["class"] == "non-commercial"
    unknown = gv.licence_entry("z", "")
    assert unknown["licence"] == "not stated" and unknown["commercial"] is False


def test_remedies_cover_every_kernel_code():
    for n in range(1, 20):
        assert gv.remedy_for(f"CLM{n:03d}"), n
    for n in range(101, 119):
        assert gv.remedy_for(f"ART{n}"), n
    assert "web access" in gv.remedy_for("perm.network.denied")
    assert "academic" in gv.remedy_for("usage.service.unknown")
    assert gv.remedy_for("SOMETHING_ELSE") == ""
