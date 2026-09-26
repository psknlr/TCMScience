"""Third-party sources as snapshots: cards, the two evidence axes, the quality gate,
deterministic ids, verified loads, skill contracts, and the HTTP backend's manners.

See docs/THIRD_PARTY_DB_CONNECTOR_SPEC.md (v2). Every test here runs offline; the HTTP
tests talk to a server on 127.0.0.1.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
from pathlib import Path

import pytest

from bioagent.sources import (SOURCE_CARDS, Access, Approval, EdgeDefault, SnapshotError,
                              SnapshotRejected, SourceCard, SourceCardError, STUDY_DESIGNS,
                              build_snapshot, card, check_claim, effective_sources,
                              licensed_claims, load_snapshot, parse_ref, validate_edge,
                              validate_node)

INCHIKEY_PUERARIN = "HKEAFJYKMMKDOR-VPRICQMDSA-N"


def node(id, category="ingredient", **kw):
    return {"id": id, "category": category, "name": id.split(":")[1], "source": "fixture", **kw}


def edge(subject="fixture:herb1", obj="fixture:cmp1", **kw):
    row = {"subject": subject, "predicate": "targets", "object": obj,
           "primary_knowledge_source": "fixture", "knowledge_level": "knowledge_assertion",
           "agent_type": "manual_agent", "study_design": "in_vitro", "license": "CC0-1.0",
           "source_record_id": "r1", "publications": ["pmid:1"]}
    row.update(kw)
    return row


def prediction(**kw):
    return edge(knowledge_level="prediction", agent_type="computational_model",
                study_design="in_silico", publications=[], **kw)


# ============================================================ schema: two evidence axes

def test_a_sound_edge_and_node_have_no_problems():
    assert validate_edge(edge()) == []
    assert validate_node(node("fixture:cmp1", xrefs={"inchikey": [INCHIKEY_PUERARIN]})) == []


def test_a_prediction_is_in_silico_and_in_silico_is_a_prediction():
    assert validate_edge(prediction()) == []
    assert any("prediction" in p for p in validate_edge(edge(knowledge_level="prediction")))
    assert any("prediction" in p for p in validate_edge(
        edge(study_design="in_silico", publications=[])))


def test_an_experiment_must_cite_and_its_tier_must_follow_its_design():
    assert any("cite" in p for p in validate_edge(edge(publications=[])))
    assert any("contradicts" in p for p in validate_edge(edge(evidence_tier="RANDOMIZED_TRIAL")))
    assert validate_edge(edge(evidence_tier="PRECLINICAL")) == []


def test_composition_edges_carry_a_level_and_c0_is_exactly_the_prediction():
    contains = edge(predicate="contains", study_design="chemical_analysis",
                    composition_level="C1", publications=[])
    assert validate_edge(contains) == []
    assert any("C0" in p for p in validate_edge({**contains, "composition_level": "C0"}))
    assert any("only applies" in p for p in validate_edge(
        edge(composition_level="C2")))
    assert any("composition" in p for p in validate_edge(
        edge(study_design="chemical_analysis", publications=[])))


def test_an_evidence_aggregate_is_an_association_that_licenses_nothing():
    assoc = edge(predicate="associated_with", study_design="evidence_aggregate",
                 knowledge_level="statistical_association",
                 agent_type="data_analysis_pipeline", publications=[])
    assert validate_edge(assoc) == []
    assert any("associated_with" in p for p in validate_edge({**assoc, "predicate": "targets"}))
    assert licensed_claims([assoc]) == frozenset()


def test_vocabularies_and_identifiers_are_checked():
    problems = validate_edge(edge(subject="not a curie", predicate="cures",
                                  knowledge_level="rumour", agent_type="oracle"))
    text = " ".join(problems)
    for word in ("CURIE", "cures", "rumour", "oracle"):
        assert word in text
    bad = validate_node(node("fixture:cmp1", xrefs={"inchikey": ["puerarin"],
                                                    "uniprot": ["P0000"]}))
    assert any("inchikey" in p for p in bad) and any("uniprot" in p for p in bad)
    assert any("missing" in p for p in validate_edge({"subject": "a:b"}))


def test_predictions_alone_carry_at_most_a_mechanism_hypothesis():
    only_predicted = [prediction(), prediction(source_record_id="r2")]
    assert licensed_claims(only_predicted) == {"mechanism_hypothesis"}
    ok, why = check_claim("mechanism", only_predicted)
    assert not ok and "mechanism_hypothesis" in why
    assert check_claim("mechanism_hypothesis", only_predicted)[0]
    assert check_claim("mechanism", only_predicted + [edge()])[0]
    ok, why = check_claim("efficacy", [edge()])
    assert not ok and "RANDOMIZED_TRIAL" in why
    assert not check_claim("mechanism", [])[0]
    composition = [edge(predicate="contains", study_design="chemical_analysis")]
    assert licensed_claims(composition) == frozenset()


def test_study_designs_are_psh_designs_plus_untiered_designs():
    ir = pytest.importorskip("psh.workflow.ir")
    assert set(STUDY_DESIGNS) - {"chemical_analysis", "evidence_aggregate"} == set(ir.DESIGNS)
    assert STUDY_DESIGNS["evidence_aggregate"] is None


# ============================================================ source cards

def _card(**kw):
    base = dict(key="fixture", name="Fixture", citation="doi:10/x", license="CC0-1.0",
                terms_url="https://example.org/terms",
                access=(Access("bulk", "https://example.org/dump.tsv"),))
    base.update(kw)
    return SourceCard(**base)


def test_web_access_needs_a_persons_approval_and_a_low_rate():
    web = Access("web", "https://example.org/search", rps=0.5)
    unapproved = _card(access=(web,))
    assert unapproved.usable_access() == () and unapproved.preferred() is None
    approved = _card(access=(web,), web_approval=Approval("reviewer", "2026-09-23",
                                                          "terms checked"))
    assert approved.preferred() is web
    with pytest.raises(SourceCardError):
        Access("web", "https://example.org/search", rps=5)
    with pytest.raises(SourceCardError):
        Approval("", "yesterday")


def test_a_card_states_licence_citation_and_access_in_preference_order():
    with pytest.raises(SourceCardError, match="licence"):
        _card(license=" ")
    with pytest.raises(SourceCardError, match="citation"):
        _card(citation="")
    with pytest.raises(SourceCardError, match="order"):
        _card(access=(Access("api", "https://example.org/api"),
                      Access("bulk", "https://example.org/dump")))
    with pytest.raises(SourceCardError, match="predictive design"):
        EdgeDefault("targets", "ingredient", "target", "prediction", "computational_model",
                    "in_vitro")


def test_record_level_licence_overrides_the_default():
    bindingdb_like = _card(license="CC-BY-4.0",
                           per_record_license={"curation_source": {"ChEMBL": "CC-BY-SA-3.0"}})
    assert bindingdb_like.record_license({"curation_source": "ChEMBL"}) == "CC-BY-SA-3.0"
    assert bindingdb_like.record_license({"curation_source": "BindingDB"}) == "CC-BY-4.0"


def test_a_skill_request_is_narrowed_never_widened():
    cards = (_card(key="open"), _card(key="off", enabled=False),
             _card(key="webonly", access=(Access("web", "https://e.org", rps=1.0),)),
             _card(key="other"))
    granted, refused = effective_sources(
        ["open@1.0", "off", "webonly", "missing", "other"], cards=cards, allowed={"open", "off"})
    assert granted == {"open": "1.0"}
    assert set(refused) == {"off", "webonly", "missing", "other"}
    assert "approval" in refused["webonly"] and "not allowed" in refused["other"]
    assert parse_ref("npass") == ("npass", "latest-approved")
    with pytest.raises(ValueError):
        parse_ref("NPASS@")


def test_the_shipped_cards_are_valid_and_conservative():
    assert {c.key for c in SOURCE_CARDS} >= {"lotus", "npass", "cmaup", "bindingdb"}
    for c in SOURCE_CARDS:
        assert c.preferred() is not None and all(a.mode != "web" for a in c.access)
        for d in c.provides:
            if d.predicate == "contains":
                assert d.study_design == "chemical_analysis" and d.composition_level == "C1"
    assert card("lotus").license == "CC-BY-4.0"     # the frozen export, not Wikidata's CC0
    assert [a.mode for a in card("bindingdb").access] == ["api", "manual"]
    assert card("bindingdb").record_license({"curation": "ChEMBL"}) == "CC-BY-SA-3.0"
    assert card("npass").name.startswith("NPASS")
    with pytest.raises(KeyError):
        card("tcmsp")


def test_an_agent_cannot_propose_changes_to_the_source_cards():
    from bioagent.evolution.boundary import BoundaryViolation, KernelBoundary
    from bioagent.runtime.component import ComponentManifest, Provider, RuntimeSpec

    proposal = ComponentManifest(
        id="evil.enable-web", kind="tool",
        runtime=RuntimeSpec(backend="python", entrypoint="bioagent.sources.cards:SOURCE_CARDS"),
        provider=Provider(source_path="src/bioagent/sources/cards.py"))
    with pytest.raises(BoundaryViolation):
        KernelBoundary().check(proposal)


# ============================================================ snapshots

def _tables():
    nodes = [node("fixture:herb1", "organism"),
             node("fixture:cmp1", xrefs={"inchikey": [INCHIKEY_PUERARIN]},
                  names={"zh": ["葛根素"], "en": ["puerarin"]}),
             node("fixture:t1", "target", xrefs={"uniprot": ["P35354"]})]
    edges = [edge(predicate="contains", study_design="chemical_analysis",
                  composition_level="C1", publications=["doi:10/abc"]),
             edge(subject="fixture:cmp1", obj="fixture:t1", source_record_id="r2",
                  measure={"type": "IC50", "relation": "=", "value": 12.5, "unit": "nM"},
                  score=0.8)]
    return nodes, edges


def _parser(raw):  # the "parser" whose source is hashed into the snapshot id
    return raw


@pytest.fixture
def raw(tmp_path):
    p = tmp_path / "raw.tsv"
    p.write_text("id\tname\ncmp1\tpuerarin\n", encoding="utf-8")
    return {"raw.tsv": p}


def _build(root, raw, **kw):
    nodes, edges = _tables()
    args = dict(key="fixture", version="1.0", nodes=nodes, edges=edges, raw_files=raw,
                parser=_parser, root=root, license="CC0-1.0", citation="doi:10/x")
    args.update(kw)
    return build_snapshot(**args)


def test_the_same_inputs_give_the_same_snapshot_id(tmp_path, raw):
    a = _build(tmp_path / "a", raw)
    time.sleep(0.01)
    nodes, edges = _tables()
    b = _build(tmp_path / "b", raw, nodes=list(reversed(nodes)), edges=list(reversed(edges)))
    assert a.snapshot_id == b.snapshot_id and a.snapshot_id.startswith("fixture@1.0#")
    changed_parser = _build(tmp_path / "c", raw, parser="def other(raw): return []")
    assert changed_parser.snapshot_id != a.snapshot_id


def test_a_loaded_snapshot_is_the_one_that_was_built(tmp_path, raw):
    built = _build(tmp_path, raw)
    loaded = load_snapshot(tmp_path, "fixture", "1.0", expected_id=built.snapshot_id)
    assert loaded.snapshot_id == built.snapshot_id and loaded.qc_status == "pass"
    measured = [e for e in loaded.edges if e.get("measure")][0]
    assert measured["measure"]["value"] == 12.5 and measured["score"] == 0.8
    assert any(n.get("names", {}).get("zh") == ["葛根素"] for n in loaded.nodes)
    with pytest.raises(SnapshotError, match="recorded"):
        load_snapshot(tmp_path, "fixture", "1.0", expected_id="fixture@1.0#000000000000")
    with pytest.raises(SnapshotError, match="no snapshot"):
        load_snapshot(tmp_path, "fixture", "9.9")


def test_a_modified_table_is_refused(tmp_path, raw):
    import pyarrow as pa
    import pyarrow.parquet as pq

    built = _build(tmp_path, raw)
    path = built.path / "edges.parquet"
    table = pq.read_table(path).to_pylist()
    table[0]["knowledge_level"] = "prediction"          # an edit after the build
    pq.write_table(pa.Table.from_pylist(table), path)
    with pytest.raises(SnapshotError, match="modified"):
        load_snapshot(tmp_path, "fixture", "1.0")


def test_a_rewritten_manifest_is_refused(tmp_path, raw):
    built = _build(tmp_path, raw)
    manifest = json.loads((built.path / "manifest.json").read_text(encoding="utf-8"))
    manifest["content"]["license"] = "anything goes"
    (built.path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SnapshotError, match="does not hash"):
        load_snapshot(tmp_path, "fixture", "1.0")


def test_structural_problems_reject_and_nothing_is_published(tmp_path, raw):
    nodes, edges = _tables()
    with pytest.raises(SnapshotRejected) as exc:
        _build(tmp_path, raw, edges=edges + [edge(obj="fixture:nowhere", source_record_id="r9")])
    assert exc.value.report.status == "fail"
    assert "not nodes" in " ".join(exc.value.report.errors)
    assert not (tmp_path / "fixture" / "1.0" / "manifest.json").exists()
    with pytest.raises(SnapshotRejected):
        _build(tmp_path, raw, edges=[{**edges[1], "license": ""}])
    with pytest.raises(SnapshotError, match="licence"):
        _build(tmp_path, raw, license="", citation="")


def test_the_gold_standard_must_resolve_exactly(tmp_path, raw):
    _build(tmp_path, raw, gold={"fixture:cmp1": {"inchikey": INCHIKEY_PUERARIN}})
    with pytest.raises(SnapshotRejected, match="gold"):
        _build(tmp_path / "x", raw, gold={"fixture:cmp1": {"inchikey": "AAAAAAAAAAAAAA-BBBBBBBBBB-N"}})


def test_low_coverage_or_drift_waits_for_a_person(tmp_path, raw):
    from bioagent.sources import QCThresholds

    first = _build(tmp_path, raw)
    nodes, edges = _tables()
    more = nodes + [node(f"fixture:cmp{i}") for i in range(2, 9)]
    second = _build(tmp_path, raw, version="2.0", nodes=more, previous=first,
                    thresholds=QCThresholds(min_inchikey_coverage=0.9))
    assert second.qc_status == "review"
    warnings = " ".join(second.manifest["content"]["qc"]["warnings"])
    assert "inchikey coverage" in warnings and "changed by" in warnings
    with pytest.raises(SnapshotError, match="awaits review"):
        load_snapshot(tmp_path, "fixture", "2.0")
    assert load_snapshot(tmp_path, "fixture", "2.0", accept_review=True).qc_status == "review"


# ============================================================ skill contracts

_CONTRACT = """id: tcm.network-pharmacology
version: 0.1.0
inputs:
  formula: FormulaRef
  indication: DiseaseOrSyndromeRef
requires:
  sources:
    - npass@2.0
    - lotus@2026-04-13
    - tcmsp
  tools:
    - stats.enrichment_analysis
max_claim_kind: mechanism_hypothesis
"""


def _skill(root: Path, name: str, contract: str | None) -> Path:
    d = root / "tcm" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: fixture skill\n---\nbody\n",
                                encoding="utf-8")
    if contract is not None:
        (d / "skill.yaml").write_text(contract, encoding="utf-8")
    return d


def test_a_skill_contract_is_read_and_can_only_ask(tmp_path):
    from bioagent.providers.skills import SkillContract

    contract = SkillContract.load(_skill(tmp_path, "np", _CONTRACT) / "skill.yaml")
    assert contract.sources == ("npass@2.0", "lotus@2026-04-13", "tcmsp")
    assert contract.inputs == {"formula": "FormulaRef", "indication": "DiseaseOrSyndromeRef"}
    granted, refused = contract.grant(allowed={"npass", "lotus"})
    assert granted == {"npass": "2.0", "lotus": "2026-04-13"}
    assert refused == {"tcmsp": "no source card"}
    assert contract.permits("mechanism_hypothesis")
    assert not contract.permits("mechanism") and not contract.permits("efficacy")


def test_the_provider_catalogues_contracts_and_flags_invalid_ones(tmp_path):
    from bioagent.providers.skills import SkillDirectoryProvider
    from bioagent.status import LifecycleState

    _skill(tmp_path, "np", _CONTRACT)
    _skill(tmp_path, "overreach", _CONTRACT.replace("mechanism_hypothesis", "cure"))
    _skill(tmp_path, "plain", None)
    found = {m.name: m for m in SkillDirectoryProvider("tcmscience", tmp_path).discover()}
    np = found["np"]
    assert np.inputs["skill_contract"]["id"] == "tcm.network-pharmacology"
    assert np.requires.services == ("npass", "lotus", "tcmsp")
    assert np.runtime.backend == "none"
    bad = found["overreach"]
    assert bad.state is LifecycleState.UNAVAILABLE and "max_claim_kind" in bad.blocking_reason
    assert found["plain"].inputs == {} and found["plain"].state is not LifecycleState.UNAVAILABLE


def test_the_default_runtime_discovers_project_skills(tmp_path):
    pytest.importorskip("psh")
    from bioagent.psh.assembly import default_runtime

    _skill(tmp_path, "np", _CONTRACT)
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills_root=tmp_path)
    assert "tcmscience.skill.np" in {m.id for m in runtime.registry}


# ============================================================ HTTP backend manners

def test_retry_after_is_read_as_seconds_or_a_date():
    from email.utils import format_datetime
    import datetime

    from bioagent.backends.http import _retry_after_seconds

    assert _retry_after_seconds({"Retry-After": "7"}) == 7.0
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=90)
    assert 60 < _retry_after_seconds({"Retry-After": format_datetime(future)}) <= 90
    assert _retry_after_seconds({"Retry-After": "soon"}) is None
    assert _retry_after_seconds({}) is None


def test_the_data_version_is_part_of_the_cache_key():
    from bioagent.backends.http import HTTPRequest

    plain = HTTPRequest(url="https://x.example/y")
    assert plain.key() == HTTPRequest(url="https://x.example/y", version="").key()
    assert plain.key() != HTTPRequest(url="https://x.example/y", version="2026-09").key()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.startswith("/slow"):
            self.send_response(429)
            self.send_header("Retry-After", "3600")
            self.end_headers()
            return
        body = ("x" * 250_000).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_a_truncated_body_is_degraded_not_succeeded(server, tmp_path):
    from bioagent.backends.http import HTTPBackend, HTTPRequest
    from bioagent.status import ExecutionStatus

    backend = HTTPBackend(cache_dir=tmp_path, rates={"127.0.0.1": 1000.0})
    req = HTTPRequest(url=server + "/big", accept="text/plain", version="v1")
    status, value, err, meta = backend.request(req)
    assert status is ExecutionStatus.DEGRADED and value["truncated"] and "truncated" in err
    assert status.successful and meta["fetched_at"]
    status, _, _, meta = backend.request(req)
    assert status is ExecutionStatus.DEGRADED and meta["cached"] and meta["fetched_at"]


def test_a_long_retry_after_ends_the_call_instead_of_being_ignored(server):
    from bioagent.backends.http import HTTPBackend, HTTPRequest
    from bioagent.status import ExecutionStatus

    backend = HTTPBackend(rates={"127.0.0.1": 1000.0}, max_retry_after_s=5)
    t0 = time.perf_counter()
    status, _, err, meta = backend.request(HTTPRequest(url=server + "/slow"))
    assert status is ExecutionStatus.FAILED and "retry after 3600s" in err
    assert meta["retry_after_s"] == 3600 and meta["attempts"] == 1
    assert time.perf_counter() - t0 < 3
