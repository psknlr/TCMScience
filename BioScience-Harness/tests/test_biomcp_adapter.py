"""BioMCP: the reviewed server configuration, and its replies as source records.

The replies are real, recorded from biomcp-python 0.7.3 on 2026-10-07 and trimmed
(``fixtures/biomcp/README.md``). The live test that starts the server and checks its
``tools/list`` against the configuration is marked ``integration``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bioagent.contracts.receipts import ContentStore
from bioagent.policy import PROFILES
from bioagent.providers.biomcp import (adapt, arguments_for, check_installed, check_listing,
                                       group_records, load_server_config, schema_digest)
from bioagent.sources.identity import SourceId, group_sources
from bioagent.status import ExecutionStatus

FIX = Path(__file__).parent / "fixtures" / "biomcp"
CONFIG = load_server_config()
ADMITTED = ("article_getter", "article_searcher", "trial_protocol_getter", "trial_searcher",
            "variant_searcher")


def _fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def _adapt(name: str, **override):
    doc = _fixture(name)
    return adapt(doc["tool"], override.get("reply", doc["reply"]), arguments=doc["arguments"],
                 retrieved_at=doc["called_at"], config=CONFIG)


def _listing() -> list[dict]:
    doc = _fixture("tools_list")
    full = {t["name"]: t for t in doc["tools"]}
    return [full.get(n, {"name": n, "inputSchema": {"type": "object"}})
            for n in doc["all_names"]]


# ------------------------------------------------------------------ the configuration
def test_the_configuration_is_a_draft_that_admits_five_tools():
    assert CONFIG.status == "draft" and CONFIG.server == "biomcp-python 0.7.3"
    assert tuple(sorted(CONFIG.tools)) == ADMITTED
    assert CONFIG.command == ("{python}", "-I", "-m", "biomcp", "run")
    assert "HTTPS_PROXY" in CONFIG.env_pass and "XDG_CACHE_HOME" in CONFIG.env_set
    assert CONFIG.destination == "PUBLIC_REMOTE"
    assert "e-mail" in CONFIG.not_admitted["trial_getter"]
    profile = PROFILES["biomedical-research"]
    for policy in CONFIG.tools.values():
        assert profile.check_network(policy.hosts).allowed, policy.name


def test_the_recorded_listing_matches_the_reviewed_schemas():
    check = check_listing(_listing(), CONFIG)
    assert check.ok and check.admitted == ADMITTED
    assert len(check.not_admitted) == 36 - 5
    assert {"trial_getter", "search", "fetch", "think"} <= set(check.not_admitted)


def test_a_changed_missing_or_duplicated_tool_is_not_admitted():
    listing = _listing()
    changed = [dict(t) for t in listing]
    for tool in changed:
        if tool["name"] == "trial_searcher":
            tool["inputSchema"] = {**tool["inputSchema"], "required": ["conditions"]}
    check = check_listing(changed, CONFIG)
    assert not check.ok and [n for n, _ in check.refused] == ["trial_searcher"]
    assert "trial_searcher" not in check.admitted
    missing = check_listing([t for t in listing if t["name"] != "article_getter"], CONFIG)
    assert missing.missing == ("article_getter",) and not missing.ok
    twice = check_listing(listing + [t for t in listing if t["name"] == "variant_searcher"],
                          CONFIG)
    assert ("variant_searcher", "listed 2 times") in twice.refused


def test_only_the_reviewed_release_may_be_started(monkeypatch):
    """Records name the configured version, so another release must not answer."""
    import importlib.metadata

    installed: dict[str, str] = {}

    def version(name):
        if name not in installed:
            raise importlib.metadata.PackageNotFoundError(name)
        return installed[name]

    monkeypatch.setattr(importlib.metadata, "version", version)
    assert check_installed(CONFIG) == "biomcp-python is not installed"
    installed["biomcp-python"] = "0.8.0"
    assert "biomcp-python 0.8.0 is installed" in check_installed(CONFIG)
    installed["biomcp-python"] = "0.7.3"
    assert check_installed(CONFIG) == ""


def test_the_schema_digest_is_over_canonical_json():
    schema = {"b": 1, "a": {"y": [1, 2], "x": "é"}}
    assert schema_digest(schema) == schema_digest(json.loads(json.dumps(schema, indent=4)))
    assert len(schema_digest(schema)) == 64


def test_arguments_are_fixed_where_reviewed_and_ignored_ones_refused():
    sent = arguments_for("article_searcher", {"chemicals": ["berberine"]}, CONFIG)
    assert sent == {"chemicals": ["berberine"], "include_cbioportal": False}
    assert arguments_for("variant_searcher", {"rsid": "rs7903146"}, CONFIG) == {
        "rsid": "rs7903146", "include_cbioportal": False, "include_oncokb": False}
    for tool, ignored in (("article_searcher", {"page_size": 3}),
                          ("trial_searcher", {"sex": "FEMALE"}),
                          ("variant_searcher", {"hgvs": "NM_004333.4:c.1799T>A"})):
        with pytest.raises(ValueError, match="accepts and ignores"):
            arguments_for(tool, ignored, CONFIG)
    with pytest.raises(ValueError, match="fixed by the review"):
        arguments_for("article_searcher", {"include_cbioportal": True}, CONFIG)
    with pytest.raises(ValueError, match="not admitted.*e-mail"):
        arguments_for("trial_getter", {"nct_id": "NCT06911983"}, CONFIG)


# ------------------------------------------------------------------ replies as records
def test_an_article_search_becomes_records_named_by_canonical_identifiers():
    reply = _adapt("article_searcher")
    assert reply.status is ExecutionStatus.SUCCEEDED and len(reply.records) == 3
    assert reply.server == "biomcp-python 0.7.3"           # not serverInfo's SDK version
    assert reply.query["chemicals"] == ["berberine"]
    assert reply.retrieved_at == _fixture("article_searcher")["called_at"]
    paper = reply.records[2]
    assert [str(i) for i in paper.identifiers] == [
        "pmid:34956436", "pmcid:PMC8696197", "doi:10.1155/2021/2074610"]
    assert paper.key == SourceId("pmid", "34956436") and paper.origin == "PubMed"
    assert paper.title.startswith("The Effect of Berberine on Metabolic Profiles")
    for record in reply.records:                       # each record is the reply's own text
        assert record.content in reply.text and record.content.startswith("# Record")


def test_trials_and_variants_are_named_by_registration_and_by_allele():
    trials = _adapt("trial_searcher")
    assert [str(r.key) for r in trials.records] == [
        "nct:NCT06911983", "nct:NCT07195994", "nct:NCT07606872"]
    protocol = _adapt("trial_protocol_getter")
    (record,) = protocol.records
    assert str(record.key) == "nct:NCT06911983" and record.content == protocol.text
    assert record.title.startswith("Comparative Efficacy of Metformin and Berberine")
    variants = _adapt("variant_searcher")
    assert [str(r.key) for r in variants.records] == [
        "hgvs:GRCh37:chr10:g.114758349C>T", "hgvs:GRCh37:chr10:g.114758349C>G"]
    assert all(SourceId("rsid", "rs7903146") in r.identifiers for r in variants.records)
    # one dbSNP record behind two alleles: one source by locus, two by allele
    assert group_records(variants.records).count == 1
    assert group_records(variants.records, link_on=("hgvs",)).count == 2


def test_an_error_returned_as_a_success_is_a_failure_with_no_records():
    reply = _adapt("article_getter_pmcid")
    assert _fixture("article_getter_pmcid")["reply"]["isError"] is False
    assert reply.status is ExecutionStatus.FAILED and reply.records == ()
    assert "Invalid identifier format: PMC8696197" in reply.reason


@pytest.mark.parametrize("text,status", [
    ("# Record 1\nError: Error 500: upstream failed\n", ExecutionStatus.FAILED),
    ("# Record 1\nError:\n  MyVariant.info API request timed out. This can happen with\n"
     "  complex queries.\n", ExecutionStatus.TIMEOUT),
    ('{\n  "error": "No studies found for NCT00000000",\n  "details": "API returned empty'
     ' studies array"\n}', ExecutionStatus.FAILED),
    ("Error: Offline mode enabled (BIOMCP_OFFLINE=true). Cannot fetch from x\n",
     ExecutionStatus.UNAVAILABLE),
])
def test_errors_rendered_as_text_are_recognised(text, status):
    reply = adapt("trial_searcher", text, arguments={}, retrieved_at="2026-10-07T00:00:00Z",
                  config=CONFIG)
    assert reply.status is status and reply.records == ()


def test_an_empty_search_succeeds_with_nothing_and_an_empty_getter_fails():
    empty = adapt("article_searcher", "\n", arguments={"genes": ["NOSUCHGENE"]},
                  retrieved_at="2026-10-07T00:00:00Z", config=CONFIG)
    assert empty.status is ExecutionStatus.SUCCEEDED and empty.records == ()
    assert empty.reason == "no records matched"
    # what trial_references_getter answered for a trial with no references (2026-10-07)
    bare = "Url: https://clinicaltrials.gov/study/NCT06911983\n\n# Protocol Section\n"
    getter = adapt("trial_protocol_getter", bare, arguments={"nct_id": "NCT06911983"},
                   retrieved_at="2026-10-07T00:00:00Z", config=CONFIG)
    assert getter.status is ExecutionStatus.FAILED and "names no record" in getter.reason


def test_every_reply_shape_the_transport_may_hand_over_reads_the_same():
    doc = _fixture("trial_searcher")
    text = doc["reply"]["content"][0]["text"]
    shapes = [doc["reply"], {"result": text}, text,
              {"content": [], "structuredContent": {"result": text}, "isError": False}]
    keys = [[str(r.key) for r in _adapt("trial_searcher", reply=s).records] for s in shapes]
    assert all(k == keys[0] for k in keys) and len(keys[0]) == 3
    failed = _adapt("trial_searcher", reply={"content": [{"type": "text", "text": "boom"}],
                                             "isError": True})
    assert failed.status is ExecutionStatus.FAILED and failed.records == ()
    unlisted = adapt("trial_getter", text, arguments={}, retrieved_at="2026-10-07T00:00:00Z",
                     config=CONFIG)
    assert unlisted.status is ExecutionStatus.DENIED
    with pytest.raises(ValueError):
        adapt("trial_searcher", text, arguments={}, retrieved_at="yesterday", config=CONFIG)
    with pytest.raises(TypeError):
        adapt("trial_searcher", 42, arguments={}, retrieved_at="2026-10-07T00:00:00Z",
              config=CONFIG)


def test_one_paper_through_biomcp_pubmed_and_open_targets_counts_once():
    """The search record names PMID, PMCID and DOI; the getter PMID and PMCID; a PubMed
    connector row the PMID; an Open Targets literature row (Europe PMC) the DOI."""
    search, getter = _adapt("article_searcher"), _adapt("article_getter")
    records = {f"biomcp.{r.tool}.{i}": r.identifiers
               for reply in (search, getter) for i, r in enumerate(reply.records)}
    records["pubmed.esearch"] = ["34956436"]                   # not a PMID until said so
    records["pubmed.esearch.typed"] = ["pmid:34956436"]
    records["opentargets.literature"] = ["https://doi.org/10.1155/2021/2074610"]
    count = group_sources(records)
    paper = count.group_of("biomcp.article_getter.0")
    assert set(paper.members) == {"biomcp.article_searcher.2", "biomcp.article_getter.0",
                                  "pubmed.esearch.typed", "opentargets.literature"}
    assert count.unidentified == ("pubmed.esearch",)
    assert count.count == 2 + 1 + 1          # two other papers, the paper, the bare number


def test_a_record_becomes_candidate_evidence_only_with_a_stated_design():
    reply = _adapt("article_searcher")
    paper = reply.records[2]
    card = reply.source_card(CONFIG)
    assert card.pinned and card.snapshot_hash == reply.content_sha256
    assert card.access_method == "mcp" and card.kind == "literature_index"
    assert card.allowed_hosts == CONFIG.tools["article_searcher"].hosts
    assert card.license_spdx == "" and card.version == "biomcp-python 0.7.3"
    store = ContentStore()
    item = paper.evidence(design="systematic_review", source_card_id=card.id,
                          quote="Doi: 10.1155/2021/2074610", store=store)
    assert (item.identifier, item.identifier_type) == ("34956436", "pmid")
    assert item.has_quote_receipt and item.verify_receipt(paper.content)
    assert item.retracted == "unverified" and item.retrieved_by.startswith("biomcp-python")
    with pytest.raises(ValueError, match="unknown study design"):
        paper.evidence(design="search_hit", source_card_id=card.id)
    with pytest.raises(ValueError, match="verbatim"):
        paper.evidence(design="systematic_review", source_card_id=card.id,
                       quote="a paraphrase of the abstract")
    variant = _adapt("variant_searcher").records[0]
    item = variant.evidence(design="observational", source_card_id="c")
    assert item.identifier_type == "registry_record"
    assert item.identifier == "hgvs:GRCh37:chr10:g.114758349C>T"


# ------------------------------------------------------------------ the live server
@pytest.mark.integration
def test_the_installed_server_lists_the_reviewed_schemas_and_answers():
    """Start the configured command over stdio, compare tools/list with the review, and
    adapt one real reply. This is a test harness, not the transport the integrator wires."""
    import asyncio
    import datetime as dt
    import os
    import sys
    import tempfile

    from omics_world import need_mcp_sdk, need_module
    need_module("biomcp")
    need_mcp_sdk()
    assert check_installed(CONFIG) == ""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def session_run(run_dir: str):
        env = {k: os.environ[k] for k in CONFIG.env_pass if k in os.environ}
        env.update({k: v.replace("{run_dir}", run_dir) for k, v in CONFIG.env_set.items()})
        command = [c.replace("{python}", sys.executable) for c in CONFIG.command]
        params = StdioServerParameters(command=command[0], args=command[1:], env=env,
                                       cwd=run_dir)
        with open(os.devnull, "w") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    when = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                    arguments = arguments_for("article_getter", {"pmid": "34956436"}, CONFIG)
                    reply = await session.call_tool("article_getter", arguments)
                    return listing, reply, when, arguments

    with tempfile.TemporaryDirectory() as run_dir:
        listing, reply, when, arguments = asyncio.run(session_run(run_dir))
    check = check_listing([t.model_dump() for t in listing.tools], CONFIG)
    assert check.ok, check
    adapted = adapt("article_getter", reply.model_dump(), arguments=arguments,
                    retrieved_at=when, config=CONFIG)
    assert adapted.status is ExecutionStatus.SUCCEEDED, adapted.reason
    assert adapted.records[0].key == SourceId("pmid", "34956436")
