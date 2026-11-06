"""The egress gate (governance.egress_check, dispatch._egress): a call whose arguments leave
the machine is refused when a string in them carries a structured identifier, before any
request, with one audit event that holds no argument text; herb names, database identifiers
and the advisory categories (name cues, mobile numbers) go out as before.

The HTTP backend is stubbed throughout: a request that reaches it is counted and answered
with a fixed Europe PMC page, so nothing leaves this machine. Two checks show the gate
changes nothing it lets through. The same calls with the gate taken out give the same
envelopes, on any tree (``test_the_gate_changes_nothing_it_lets_through``). And
egress_golden.json holds the envelopes recorded on 530031f, before the gate, with the
volatile and version-bound fields removed (``normalise``). After a deliberate change to
what those envelopes say (another change to the dispatcher's envelopes, say), rewrite the
file with ``python studio/runner/tests/test_egress_gate.py`` and review the rewrite as a
diff: the file then no longer shows the envelopes from before the gate, and that diff
must show only the other change.
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

import pytest

from tcmstudio import capability
from tcmstudio import governance as gv
from tcmstudio.catalog import entries, get_entry
from tcmstudio.dispatch import call

GOLDEN = Path(__file__).with_name("egress_golden.json")

#: Through the gate unchanged: a herb, a compound and its enzyme, literature, a DOI, a trial,
#: a GEO series, a SMILES string, an eleven-digit database id the mobile rule reads as a phone
#: number, and a classical author before his formula the name rule reads as a person.
PASSING = ("黄芪", "berberine CYP3A4", "PMID 31415926", "10.1016/j.jep.2020.112345", "NCT04280705",
           "GSE123456", "CC(=O)OC1=CC=CC=C1C(=O)O", "PubChem CID 13912345678", "王清任 血府逐瘀汤")

#: Refused, with the one category each carries.
REFUSED = (("Patient Alice Smith MRN 04851923 hepatotoxicity", "medical_record_number"),
           ("身份证号110101199003071234", "chinese_resident_id"),
           ("a@b.com", "email_address"))
#: What in each refused query identifies someone: never written anywhere.
SECRET = {"Patient Alice Smith MRN 04851923 hepatotoxicity": ("Alice", "Smith", "04851923"),
          "身份证号110101199003071234": ("110101199003071234",), "a@b.com": ("a@b.com",)}

#: The routes by which a query reaches connector.europepmc.search (the last also esearch).
ROUTES = {
    "call_tool": lambda q: ("call_tool", {"tool": "connector.europepmc.search",
                                          "arguments": {"query": q}}),
    "entry_id": lambda q: ("connector.europepmc.search", {"query": q}),
    "connector_call": lambda q: ("connector_call", {"connector": "europepmc", "operation": "search",
                                                    "arguments": {"query": q}}),
    "literature_search": lambda q: ("literature_search", {"query": q}),
    "literature_search_pubmed": lambda q: ("literature_search", {"query": q, "source": "pubmed"}),
}


@contextlib.contextmanager
def stubbed_http() -> Iterator[list[str]]:
    """Every HTTP request of the connectors answered here, never sent: the URLs, in order."""
    from bioagent.backends import http as H
    sent: list[str] = []

    def request(self, req, *, use_cache=True, allowed_hosts=None):
        sent.append(req.full_url)
        if "esearch" in req.full_url:
            value: Any = {"esearchresult": {"count": "0", "idlist": []}}
        else:
            value = {"hitCount": 1, "resultList": {"result": [
                {"title": "Hepatotoxicity of herbal medicines", "pmid": "31415926",
                 "doi": "10.1016/j.jep.2020.112345", "journalTitle": "J Ethnopharmacol",
                 "pubYear": "2020", "authorString": "Doe J, Roe R", "source": "MED"}]}}
        meta = {"url": req.full_url, "method": req.method, "host": req.host, "cached": False,
                "attempts": 1, "http_status": 200}
        return (*H.HTTPBackend._judge(value), meta)

    saved = H.HTTPBackend.request
    H.HTTPBackend.request = request
    try:
        yield sent
    finally:
        H.HTTPBackend.request = saved


@pytest.fixture
def http():
    with stubbed_http() as sent:
        yield sent


def online(tmp_path, where: str = "runner") -> dict[str, Any]:
    return {"where": where, "state_root": str(tmp_path / "projects"), "network": True,
            "project_id": "p-egress", "tcmdb_root": str(tmp_path / "tcmdb")}


def chain(ctx: dict[str, Any]) -> Path:
    return Path(ctx["state_root"]) / ctx["project_id"] / "psh" / "events.db"


def audit_events(ctx: dict[str, Any]) -> list[Any]:
    from psh.kernel.events import EventStore
    if not chain(ctx).is_file():
        return []
    store = EventStore(chain(ctx))
    try:
        return store.records()
    finally:
        store.close()


# ------------------------------------------------------------------------- refusals

@pytest.mark.parametrize("query,category", REFUSED)
@pytest.mark.parametrize("route", sorted(ROUTES))
def test_an_identifier_is_refused_before_any_request(tmp_path, http, route, query, category):
    ctx = online(tmp_path)
    tool, args = ROUTES[route](query)
    e = call(tool, args, ctx)
    assert e["status"] == "refused" and e["error"]["type"] == "refused", (e["status"], e["error"])
    ref = e["governance"]["refusals"]
    assert [r["code"] for r in ref] == ["EGRESS_IDENTIFIER"]
    assert ref[0]["categories"] == [category]
    assert ref[0]["remedy"] == gv.remedy_for("EGRESS_IDENTIFIER") == e["error"]["hint"]
    assert http == [], "the stubbed backend was never asked"
    assert e["receipt"]["result"]["outcome"] == "not_executed"
    assert e["receipt"]["execution"]["implementation"] is None
    # the categories are named, the identifier never
    shown = json.dumps(e, ensure_ascii=False)
    assert category in e["summary_en"] and category in e["text"]
    for part in SECRET[query]:
        assert part not in shown, part


@pytest.mark.parametrize("query,category", REFUSED)
def test_one_audit_event_and_no_argument_text_in_it(tmp_path, http, query, category):
    from tcmstudio.envelope import sha256_json
    ctx = online(tmp_path)
    e = call("call_tool", {"tool": "connector.europepmc.search", "arguments": {"query": query}}, ctx)
    assert e["status"] == "refused" and http == []
    events = audit_events(ctx)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "egress_refused" and ev.status == "refused"
    assert ev.component_id == "connector.europepmc.search"
    assert ev.detail["categories"] == [category]
    assert ev.detail["entry"] == "connector.europepmc.search"
    assert ev.detail["hosts"] == ["www.ebi.ac.uk"]
    assert ev.detail["where"] == "runner"
    # the hash an auditor matches with the envelope's own record of the arguments
    assert ev.detail["input_sha256"] == sha256_json({"query": query}) \
        == e["receipt"]["execution"]["validation"]["normalized_sha256"]
    # not in the event, and not anywhere in the chain's file
    raw = chain(ctx).read_bytes()
    record = json.dumps(ev.detail, ensure_ascii=False)
    for part in (query, *SECRET[query]):
        assert part not in record and part.encode("utf-8") not in raw, part
    # and the chain still verifies, read as the audit tool reads it
    v = call("system.audit_verify", {}, ctx)["result"]
    assert v["intact"] is True and v["records"] == 1 and v["events"] == {"egress_refused": 1}


def test_refused_with_web_access_off_too_and_in_the_browser(tmp_path, http):
    """The gate comes before web access and before where the call runs: the refusal is the
    same with web access off (not network_off), and in a browser context."""
    query = REFUSED[0][0]
    keep = ("status", "summary", "summary_en", "text", "error", "governance", "result", "citations")
    seen = []
    for where in ("runner", "browser"):
        for network in (True, False):
            ctx = {**online(tmp_path / f"{where}-{network}", where), "network": network}
            e = call("literature_search", {"query": query}, ctx)
            assert e["status"] == "refused", (where, network, e["error"])
            assert e["receipt"]["where"] == where
            assert len(audit_events(ctx)) == 1
            seen.append({k: e[k] for k in keep})
    assert all(s == seen[0] for s in seen[1:])
    assert http == []


def test_every_network_entry_is_gated(tmp_path, http, jobs):
    """Every kind that reaches the network: a reviewed provider wrapper, a job that sends a
    sequence out (only when allow_remote is set), a connector called by its id."""
    ctx = {**online(tmp_path), "jobs": jobs}
    wrapped = call("call_tool", {"tool": "system.provider_call",
                                 "arguments": {"provider": "tooluniverse",
                                               "tool": "EuropePMC_search_articles",
                                               "arguments": {"query": "contact a@b.com"}}}, ctx)
    assert wrapped["status"] == "refused", wrapped["error"]
    assert wrapped["governance"]["refusals"][0]["categories"] == ["email_address"]
    fasta = ">query from a@b.com\nMKTAYIAKQRQISFVKSHFSRQ"
    remote = call("run_pipeline", {"pipeline": "fold", "arguments": {"source": fasta,
                                                                    "allow_remote": True}}, ctx)
    assert remote["status"] == "refused" and remote["via"] == "job.pipeline.fold", remote["error"]
    assert jobs.submitted == []
    # folded on this machine, the same FASTA stays here: queued, not refused
    local = call("run_pipeline", {"pipeline": "fold", "arguments": {"source": fasta}}, ctx)
    assert local["status"] == "job_submitted" and len(jobs.submitted) == 1, local["error"]
    key = call("connector.europepmc.search", {"query": "sk-abcdefghijklmnop1234 TP53"}, ctx)
    assert key["governance"]["refusals"][0]["categories"] == ["api_key"]
    assert http == []
    assert [ev.component_id for ev in audit_events(ctx)] == [
        "system.provider_call", "job.pipeline.fold", "connector.europepmc.search"]


def test_a_skill_run_as_a_job_is_gated_as_the_skill_it_runs(tmp_path, http, jobs):
    """Review of 2026-11-06: job.skill.run carries the skill's arguments to the job, and the
    job sends what the skill sends; the gate read job.skill.run's own flags (no network), so
    the job was queued where the same skill called directly was refused."""
    from tcmstudio.envelope import sha256_json
    ctx = {**online(tmp_path), "jobs": jobs}
    fasta = ">query from a@b.com\nMKTAYIAKQRQISFVKSHFSRQ"
    direct = call("skill.predict-protein-structure", {"sequences": fasta, "allow_remote": True},
                  ctx)
    args ={"skill_id": "predict-protein-structure",
            "arguments": {"sequences": fasta, "allow_remote": True}}
    via_job = call("job.skill.run", args, ctx)
    for e in (direct, via_job):
        assert e["status"] == "refused", e["error"]
        assert e["governance"]["refusals"][0]["code"] == "EGRESS_IDENTIFIER"
        assert e["governance"]["refusals"][0]["categories"] == ["email_address"]
    assert jobs.submitted == [] and http == []
    event = audit_events(ctx)[-1]
    assert event.component_id == "job.skill.run"
    assert event.detail["hosts"] == get_entry("skill.predict-protein-structure")["hosts"]
    assert event.detail["input_sha256"] == sha256_json(args) \
        == via_job["receipt"]["execution"]["validation"]["normalized_sha256"]
    # folded on this machine, the same sequence stays here: queued
    local = call("job.skill.run", {"skill_id": "predict-protein-structure",
                                   "arguments": {"sequences": fasta}}, ctx)
    assert local["status"] == "job_submitted" and len(jobs.submitted) == 1, local["error"]


def test_a_skill_run_as_a_job_needs_web_access_as_the_skill_does(tmp_path, jobs):
    """The same reading decides web access (capability.requirements): with it off, a job that
    would send a sequence out is network_off as the skill called directly is, not queued."""
    ctx = {**online(tmp_path), "jobs": jobs, "network": False}
    clean = {"sequences": ">q\nMKTAYIAKQRQISFVKSHFSRQ", "allow_remote": True}
    direct = call("skill.predict-protein-structure", clean, ctx)
    via_job = call("job.skill.run", {"skill_id": "predict-protein-structure", "arguments": clean},
                   ctx)
    for e in (direct, via_job):
        assert e["status"] == "failed" and e["error"]["type"] == "network_off", e["error"]
        assert e["error"]["requires"][0]["names"] == ["api.esmatlas.com", "files.rcsb.org",
                                                      "alphafold.ebi.ac.uk"]
    assert jobs.submitted == []
    # folded on this machine it needs no web access
    local = call("job.skill.run", {"skill_id": "predict-protein-structure",
                                   "arguments": {"sequences": ">q\nMKTAYIAKQRQISFVKSHFSRQ"}}, ctx)
    assert local["status"] == "job_submitted", local["error"]


def test_the_audit_hash_is_the_one_the_envelope_records_by_every_route(tmp_path, http):
    """Review of 2026-11-06: literature_search hashed the connector arguments it builds
    ({query, page_size}; {db, term, retmax} for PubMed), which match nothing in its envelope."""
    for route in sorted(ROUTES):
        ctx = online(tmp_path / route)
        tool, args = ROUTES[route]("a@b.com")
        e = call(tool, args, ctx)
        assert e["status"] == "refused", (route, e["error"])
        [event] = audit_events(ctx)
        assert event.detail["input_sha256"] == \
            e["receipt"]["execution"]["validation"]["normalized_sha256"], route
    assert http == []


def test_what_stays_on_the_machine_is_not_gated(tmp_path, runner_ctx):
    """A local tool's arguments never leave: an identifier in them is that tool's business
    (the clinic's intake carries patient data by design)."""
    e = call("tcm_herb", {"name": "身份证号110101199003071234"}, runner_ctx)
    assert e["status"] != "refused"
    assert not chain(runner_ctx).is_file()


def test_the_browser_worker_writes_the_event_inside_a_sync_of_its_own(tmp_path, http, monkeypatch):
    """pyodide.worker.js syncs a project's files with IndexedDB around stateful calls only, so
    an event a connector call wrote would be deleted by the next stateful call's sync in (the
    browser sweep's audit_verify counted 42 records where native CPython counted 45). The worker
    holds the event (defer_audit) and writes it between a sync in and out (flush_audit) before
    it returns the envelope."""
    from tcmstudio import dispatch
    ctx = online(tmp_path, "browser")
    dispatch.defer_audit(True)
    try:
        e = call("literature_search", {"query": "a@b.com"}, ctx)
        assert e["status"] == "refused" and http == []
        assert dispatch.audit_pending() and audit_events(ctx) == []
        assert dispatch.flush_audit() == 1
        assert not dispatch.audit_pending() and dispatch.flush_audit() == 0
        [event] = audit_events(ctx)
        assert event.detail["where"] == "browser" and event.detail["categories"] == ["email_address"]
        # an event that cannot be written stays held, with those after it, and the error is raised
        call("literature_search", {"query": "SSN 123-45-6789"}, ctx)
        call("literature_search", {"query": "身份证号110101199003071234"}, ctx)
        real = dispatch._append_event
        monkeypatch.setattr(dispatch, "_append_event", lambda *a: (_ for _ in ()).throw(OSError("quota")))
        with pytest.raises(OSError):
            dispatch.flush_audit()
        assert dispatch.audit_pending()
        monkeypatch.setattr(dispatch, "_append_event", real)
        assert dispatch.flush_audit() == 2
        assert [ev.detail["categories"] for ev in audit_events(ctx)] == [
            ["email_address"], ["social_security_number"], ["chinese_resident_id"]]
    finally:
        dispatch.defer_audit(False)
    # the Worker turns it on and flushes after a call that is not stateful and inside a stateful
    # one: read in its source here; what shows it works is the browser sweep, whose
    # direct:system.audit_verify differs when the Worker does not defer
    worker =(Path(__file__).resolve().parents[2] / "web" / "js" / "runtime"
              / "pyodide.worker.js").read_text(encoding="utf-8")
    assert "_dispatch.defer_audit(True)" in worker
    assert "if (api.auditPending?.()) return { text, persist: await flushAudit() };" in worker
    assert worker.count("api.flushAudit") >= 3


def test_a_refusal_that_cannot_be_recorded_still_refuses(tmp_path, http, monkeypatch):
    from tcmstudio import dispatch

    def broken(*args, **kwargs):
        raise OSError("read-only file system")
    monkeypatch.setattr(dispatch, "_egress_audit", broken)
    e = call("literature_search", {"query": "a@b.com"}, online(tmp_path))
    assert e["status"] == "refused" and http == []
    assert "could not be written to the project's audit chain (OSError: read-only file system)" \
        in e["text"]


# ------------------------------------------------------------------- what goes out

@pytest.mark.parametrize("query", [*PASSING, "患者王清任 男", "Patient Alice Smith hepatotoxicity",
                                   "berberine 15801234567 mg"])
def test_advisory_categories_and_research_identifiers_go_out(tmp_path, http, query):
    """The name cues and the mobile rule stay advisory (governance.HARD_IDENTIFIERS)."""
    ctx = online(tmp_path)
    e = call("literature_search", {"query": query}, ctx)
    assert e["status"] == "succeeded", e["error"]
    assert len(http) == 1
    assert not chain(ctx).is_file(), "nothing refused, nothing recorded"


def normalise(envelope: dict[str, Any]) -> dict[str, Any]:
    """An envelope without what differs from run to run or host to host: the duration, the
    times, the interpreter and package versions (the implementation's among them: a
    BioScience release changes it, not the gate), and the operation's status record (its
    times, and the committed production record it merges)."""
    env = json.loads(json.dumps(envelope, ensure_ascii=False))
    env.pop("duration_ms", None)
    for key in ("runtime", "versions", "started_at", "finished_at", "operational"):
        env["receipt"].pop(key, None)
    implementation = (env["receipt"].get("execution") or {}).get("implementation")
    if isinstance(implementation, dict):
        implementation.pop("version", None)
    return env


def golden_envelopes(root: Path) -> dict[str, Any]:
    """Each passing query by literature_search and by connector_call, with web access on and
    the HTTP backend stubbed: {"<route> <query>": normalised envelope}."""
    out: dict[str, Any] = {}
    with stubbed_http():
        for i, query in enumerate(PASSING):
            for route in ("literature_search", "connector_call"):
                ctx = online(root / f"{i}-{route}")
                tool, args = ROUTES[route](query)
                out[f"{route} {query}"] = normalise(call(tool, args, ctx))
    return out


def test_the_gate_changes_nothing_it_lets_through(tmp_path, monkeypatch):
    """The envelopes of the passing queries, with the gate and with it taken out, in this
    tree: unlike the golden copies, this holds whatever else changes the envelopes."""
    from tcmstudio import dispatch
    gated = golden_envelopes(tmp_path / "gated")
    monkeypatch.setattr(dispatch, "_egress", lambda *args, **kwargs: None)
    assert golden_envelopes(tmp_path / "ungated") == gated


def test_golden_envelopes_are_unchanged(tmp_path):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    fresh = golden_envelopes(tmp_path)
    assert sorted(fresh) == sorted(golden)
    for key in golden:
        assert fresh[key] == golden[key], key
    assert all(e["status"] == "succeeded" for e in fresh.values())


def test_no_catalog_example_carries_an_identifier():
    """Every networked entry's and core tool's own example goes out (the browser sweep and
    the offline connector test call them with web access off and expect network_off)."""
    from tcmstudio.core_tools import CORE_BY_NAME
    checked = 0
    for where in ("runner", "browser"):
        for entry in entries(where, probe=False):
            example = entry.get("example")
            if isinstance(example, dict) and capability.reaches_network(entry, example):
                assert gv.egress_categories(example) == [], entry["id"]
                checked += 1
    for name, core in CORE_BY_NAME.items():
        if isinstance(core.get("example"), dict):
            assert gv.egress_categories(core["example"]) == [], name
    assert checked >= 800


# ------------------------------------------------------------------------ the gate

def test_string_values_are_read_never_keys_or_numbers():
    assert gv.egress_categories({"a@b.com": "berberine", "mrn": 4851923}) == []
    assert gv.egress_categories({"q": [{"x": ("身份证号110101199003071234",)}]}) == \
        ["chinese_resident_id"]
    assert gv.egress_categories(["SSN 123-45-6789", "Bearer abcdefghijklmnopqrstuvwxyz",
                                 "-----BEGIN RSA PRIVATE KEY-----", "住院号：12345678",
                                 "ghp_abcdefghijklmnopqrstuvwxyz"]) == \
        ["api_key", "bearer_token", "chinese_record_number", "private_key_block",
         "social_security_number"]
    assert gv.egress_categories("") == gv.egress_categories(None) == []


def test_a_long_argument_is_read_in_time():
    """Review of 2026-11-06: every argument is classified whole, and the classifier's email
    rule read a long run of [\\w.+-] in quadratic time ('a.a.a.…': 12 s at 80 KB, 2 MB not
    done in five minutes) before the call started; it is linear now (psh.kernel.classify)."""
    import time
    for size, limit in ((40_000, 2), (1_000_000, 20)):
        t0 = time.perf_counter()
        assert gv.egress_categories({"x": "a." * size, "y": ["write to a.b@c.org"]}) == \
            ["email_address"]
        assert time.perf_counter() - t0 < limit, size


def test_the_set_is_configurable_and_the_refusal_names_no_value():
    assert gv.egress_check("connector.x", {"q": "13912345678"}) is None
    r = gv.egress_check("connector.x", {"q": "13912345678"},
                        identifiers={"chinese_mobile_number"})
    assert r["code"] == "EGRESS_IDENTIFIER" and r["categories"] == ["chinese_mobile_number"]
    assert r["entry"] == "connector.x" and "13912345678" not in json.dumps(r)
    assert gv.HARD_IDENTIFIERS.isdisjoint({"name_cued", "name_cued_cjk", "chinese_patient_name",
                                           "chinese_mobile_number", "phone"})


def test_the_gate_reads_the_classifiers_built_in_rules_wherever_sable_is_installed():
    """The browser never has sable: the runner must not refuse what the browser lets out,
    nor the reverse, and HARD_IDENTIFIERS are the built-in rules' names."""
    from psh.kernel import classify
    gv.egress_categories("x")
    assert gv._EGRESS_CLASSIFIER.detector_name == "psh.fallback"
    built_in = {name for name, _, _ in classify._FALLBACK_RULES} | {
        name for name, _ in classify._SECRET_RULES}
    assert gv.HARD_IDENTIFIERS <= built_in


def test_reaches_network_is_the_routers_flag():
    fold = get_entry("job.pipeline.fold")
    assert not capability.reaches_network(fold, {"source": "x"})
    assert capability.reaches_network(fold, {"source": "x", "allow_remote": True})
    models = get_entry("job.admet.models")
    assert capability.reaches_network(models, {})
    assert not capability.reaches_network(models, {"archive": "f_1"})
    assert capability.reaches_network(get_entry("connector.europepmc.search"), {"query": "x"})
    assert not capability.reaches_network(get_entry("native.tcm_herb"), {"name": "x"})
    # job.skill.run, given the catalog, reaches what the skill it runs reaches with its arguments
    run = get_entry("job.skill.run")
    remote = {"skill_id": "predict-protein-structure",
              "arguments": {"sequences": "x", "allow_remote": True}}
    assert not capability.reaches_network(run, remote)
    assert capability.reaches_network(run, remote, entry_of=get_entry)
    assert not capability.reaches_network(run, {**remote, "arguments": {"sequences": "x"}},
                                          entry_of=get_entry)
    assert not capability.reaches_network(run, None, entry_of=get_entry)
    assert capability.hosts_for(run, remote, entry_of=get_entry) == \
        ["api.esmatlas.com", "files.rcsb.org", "alphafold.ebi.ac.uk"]
    assert capability.carried(run, remote, get_entry) == (
        get_entry("skill.predict-protein-structure"), remote["arguments"])


if __name__ == "__main__":
    # rewrite egress_golden.json from this tree (see the module's docstring)
    with tempfile.TemporaryDirectory() as tmp:
        doc = golden_envelopes(Path(tmp))
    GOLDEN.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"{GOLDEN}: {len(doc)} envelopes", file=sys.stderr)
