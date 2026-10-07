"""Per-operation governance inside a governed skill run, and real destinations in programs.

``run_governed`` checked a skill's manifest, pin and artifact and then ran
``fn(**arguments)``: what the skill did inside — an RCSB download, a sequence sent to the
ESM Atlas — was not checked against the manifest, not executed through either kernel and
not recorded. And ``skill_program`` compiled every tool step as local compute, whatever
the component did. These tests hold both to what the code now does:

* an undeclared operation is refused and recorded;
* a declared remote operation is executed through PSH's broker and BioScience's runtime and
  recorded with its real destination, its status and the digest of its output;
* a failed, refused or unbrokered operation blocks release even when the skill carries on;
* a protein sequence in the fold's declared ``sequence`` field reaches the public service
  whatever its length, and anything else put in that field is refused;
* the offline P0 skills, which perform no operation, are still released;
* ``skill_program`` derives a remote destination from an HTTP component and refuses one
  the run does not authorise.

No test here touches the network except the three marked ``integration``: downloads are
answered by a canned ``urllib`` opener that still raises the ``urllib.Request`` audit
event, so the egress guard sees every request exactly as it would a real one.
"""

from __future__ import annotations

import base64
import dataclasses as dc
import email.message
import hashlib
import io
import json
import socket
import sqlite3
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path

import pytest
import yaml

from bioagent.contracts import SourceCard, validate_artifact
from bioagent.contracts.artifact import ResearchArtifact
from bioagent.contracts.attestation import AuditChainAttestor, operation_digest
from bioagent.contracts.receipts import default_store
from bioagent.governed import run_governed
from bioagent.operations import (ALPHAFOLD_MODEL, COLABFOLD_BATCH, ESMATLAS_FOLD,
                                 ESMFOLD_WEIGHTS, RCSB_CHEMCOMP, RCSB_ENTRY, OperationBroker,
                                 OperationError, OperationRefused, current_broker,
                                 fetch_bytes, operation)
from bioagent.skills.loader import load_skill_dir
from bioagent.updates.registry import version_from_spec

REPO = Path(__file__).resolve().parents[1]
P0 = REPO / "skills" / "tcm"
MOLECULAR = REPO / "skills" / "candidates" / "molecular"
FIX = Path(__file__).parent / "fixtures" / "structure"

ENTRY_URL = "https://files.rcsb.org/download/1UBQ.pdb"
CHEMCOMP_URL = "https://data.rcsb.org/rest/v1/core/chemcomp/BEN"
REDIRECT_URL = "https://files.rcsb.org/download/MOVED.pdb"
ENTRY = b"HEADER    CANNED ENTRY\nEND\n"
UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
#: hen egg-white lysozyme, 129 residues: long enough that PSH would floor it as
#: uninspectable content if the fold did not declare its field a protein sequence
LYSOZYME = ("KVFGRCELAAAMKRHGLDNYRGYSLGNWVCAAKFESNFNTQATNRNTDGSTDYGILQINSRWWCNDGRTPGSRNLC"
            "NIPCSALLSSDITASVNCAKKIVSDGNGMNAWVAWRNRCKGTDVQAWIRGCRL")


# ------------------------------------------------------------------ the canned network

class _Canned(urllib.request.BaseHandler):
    """Answers https requests from a table instead of a socket: a body, an exception to
    raise, or another URL to redirect to (opened through the same opener, so the redirect
    is audited like a real one)."""

    def __init__(self, table):
        self.table = table
        self.opened: list[str] = []

    def https_open(self, req):
        self.opened.append(req.full_url)
        answer = self.table[req.full_url]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, str):
            return self.parent.open(answer, timeout=req.timeout)
        return urllib.response.addinfourl(io.BytesIO(answer), email.message.Message(),
                                          req.full_url, 200)


@pytest.fixture
def canned(monkeypatch):
    def install(table):
        handler = _Canned(table)
        opener = urllib.request.OpenerDirector()
        opener.add_handler(handler)
        monkeypatch.setattr(urllib.request, "_opener", opener)
        return handler
    return install


# ------------------------------------------------------------------ a probe skill

PROBE = "probe-operations"
PROBE_YAML = """api_version: "1"
id: probe-operations
name: Probe operations
version: "0.1.0"
summary: performs one external operation, for the broker to rule on
maintainer: TCMScience tests
license_spdx: MIT
integration_mode: native
runtime:
  backend: python
  entrypoint: "test_operation_broker:probe_operations"
permissions:
  network: [files.rcsb.org]
  subprocess: false
evidence:
  max_tier: preclinical
  claim_kinds: [mechanism_hypothesis]
outputs:
  type: object
risk: r1_routine
min_autonomy: observe
"""
_CARD = SourceCard(id="test.probe", name="probe input", kind="dataset", maintainer="tests",
                   access_method="local_file", license_spdx="MIT", integration_mode="native",
                   offline_capable=True, snapshot_hash="0" * 64,
                   snapshot_at="2026-10-07T00:00:00+00:00")


def probe_operations(mode: str = "declared", *, run_id: str = "") -> ResearchArtifact:
    """Perform the operation ``mode`` names; carry on whatever happens, as a careless
    skill would, so that release is decided by the run and not by the skill's error."""
    from bioagent.skills.base import artifact

    try:
        if mode == "declared":
            operation(RCSB_ENTRY, fetch_bytes, url=ENTRY_URL, timeout=5)
        elif mode == "undeclared":
            operation(RCSB_CHEMCOMP, fetch_bytes, url=CHEMCOMP_URL, timeout=5)
        elif mode == "redirected":
            operation(RCSB_ENTRY, fetch_bytes, url=REDIRECT_URL, timeout=5)
        elif mode == "unbrokered":
            urllib.request.urlopen(ENTRY_URL, timeout=5)              # noqa: S310
        elif mode == "socket":
            sock = socket.socket()
            try:
                sock.connect(("192.0.2.1", 80))                      # TEST-NET-1
            finally:
                sock.close()
        outcome = "ran"
    except (OperationError, PermissionError) as exc:
        outcome = f"carried on after {type(exc).__name__}"
    return artifact(id=PROBE, run_id=run_id, skill_id=PROBE, skill_version="0.1.0",
                    question="does the broker see this operation?", sources=(_CARD,),
                    limitations=("a test skill: its only product is the operation it tried",),
                    provenance={"outcome": outcome})


def _pin(lockfile: Path, *skill_dirs: Path) -> Path:
    """A lockfile pinning each skill as it is on disk now."""
    versions = [version_from_spec(load_skill_dir(d).spec).as_dict() for d in skill_dirs]
    lockfile.write_text(yaml.safe_dump({"api_version": "1", "skills": versions}),
                        encoding="utf-8")
    return lockfile


@pytest.fixture
def probe(tmp_path):
    """Run the probe skill, pinned, in its own state directory; returns (run, state_dir)."""
    skills = tmp_path / "skills"
    (skills / PROBE).mkdir(parents=True)
    (skills / PROBE / "skill.yaml").write_text(PROBE_YAML, encoding="utf-8")
    lock = _pin(tmp_path / "skills.lock.yaml", skills / PROBE)

    def run(mode: str):
        state = tmp_path / mode / "psh"
        result = run_governed(PROBE, {"mode": mode}, skill_dir=skills, lockfile=lock,
                              state_dir=state, output_dir=tmp_path / mode / "out",
                              callables={PROBE: probe_operations})
        return result, state
    return run


def _events(state_dir: Path) -> list[tuple[str, dict]]:
    with sqlite3.connect(state_dir / "events.db") as db:
        rows = db.execute("SELECT event_type, detail FROM events ORDER BY seq").fetchall()
    return [(kind, json.loads(detail or "{}")) for kind, detail in rows]


def _operation_events(state_dir: Path) -> list[dict]:
    return [d for kind, d in _events(state_dir) if kind == "bioscience_operation"]


# ======================================================== a declared remote operation

def test_a_declared_remote_operation_is_recorded_with_its_destination_and_status(
        probe, canned):
    network = canned({ENTRY_URL: ENTRY})
    run, state = probe("declared")
    assert network.opened == [ENTRY_URL]
    (entry,) = run.operations
    assert entry["operation"] == RCSB_ENTRY and entry["declared"] is True
    assert entry["implementation"] == "bioagent.operations:fetch_bytes"
    # what the contract required: the host, the destinations the bridge derives, the ceiling
    assert entry["hosts"] == ["files.rcsb.org"]
    assert entry["destinations"] == ["LOCAL_COMPUTE", "PUBLIC_REMOTE"]
    assert entry["label_ceiling"] == "RESEARCH_DEIDENTIFIED"
    # what ruled, and how it ended
    assert entry["checks"] == ["skill_manifest:passed", "psh_broker:passed",
                               "bioscience_resolver:passed", "bioscience_policy:passed"]
    assert entry["status"] == "SUCCEEDED" and entry["reason"] == ""
    assert entry["output_sha256"] == hashlib.sha256(ENTRY).hexdigest()
    assert entry["recorded"] is True
    assert entry["evidence_sha256"] == operation_digest(entry)
    # in the chain: our record, and PSH's own egress decision on the real destination
    (recorded,) = _operation_events(state)
    assert recorded["evidence_sha256"] == entry["evidence_sha256"]
    assert recorded["run_anchor"] == run.artifact.provenance["governed"]["run_anchor"]
    events = _events(state)
    assert any(kind == "egress_decision" and d.get("destination") == "PUBLIC_REMOTE"
               for kind, d in events)
    assert any(kind == "tool_call" and d.get("execution") == "in_process"
               for kind, d in events)
    assert run.artifact.policy_id == "default+declared-remote"
    assert run.released, run.verdict.as_dict()


def test_the_released_artifact_is_attested_with_its_operations(probe, canned):
    canned({ENTRY_URL: ENTRY})
    run, state = probe("declared")
    imported = ResearchArtifact.from_dict(json.loads(json.dumps(run.artifact.document(),
                                                                default=str)))
    with AuditChainAttestor.open(state) as attestor:
        assert attestor.attest(imported) == (True, "")
        verdict = validate_artifact(imported, output_root=run.output_dir,
                                    content_store=default_store, attestor=attestor)
    assert verdict.release_authorized


# ======================================================== refused or failed operations

def test_an_undeclared_operation_is_refused_and_recorded(probe, canned):
    network = canned({CHEMCOMP_URL: b"{}"})
    run, state = probe("undeclared")
    assert network.opened == []                       # refused before anything was sent
    (entry,) = run.operations
    assert entry["operation"] == RCSB_CHEMCOMP and entry["declared"] is False
    assert entry["status"] == "DENIED" and entry["checks"] == ["skill_manifest:refused"]
    assert "data.rcsb.org" in entry["reason"] and entry["recorded"] is True
    assert [d["evidence_sha256"] for d in _operation_events(state)] == [
        entry["evidence_sha256"]]
    assert run.artifact.provenance["outcome"] == "carried on after OperationRefused"
    assert not run.released and "ART118" in run.verdict.codes


def test_a_failed_operation_blocks_release(probe, canned):
    canned({ENTRY_URL: urllib.error.URLError("simulated outage")})
    run, state = probe("declared")
    (entry,) = run.operations
    assert entry["status"] == "FAILED" and "simulated outage" in entry["reason"]
    assert "psh_broker:passed" in entry["checks"]       # it ran, and the service failed
    assert run.artifact.provenance["outcome"] == "carried on after OperationError"
    assert not run.released and not run.verdict.execution_attested
    assert "ART118" in run.verdict.codes
    governed = run.artifact.provenance["governed"]
    assert governed["development_run"] is False         # pinned: blocked by the operation
    assert any("FAILED" in reason for reason in governed["release_refused"])
    kinds = [kind for kind, _ in _events(state)]
    assert "bioscience_artifact_not_attested" in kinds
    assert "bioscience_artifact_attested" not in kinds


def test_a_redirect_to_an_undeclared_host_is_refused(probe, canned):
    network = canned({REDIRECT_URL: "https://mirror.example.org/1UBQ.pdb",
                      "https://mirror.example.org/1UBQ.pdb": ENTRY})
    run, _ = probe("redirected")
    assert network.opened == [REDIRECT_URL]             # the mirror was never contacted
    (entry,) = run.operations
    assert entry["status"] == "DENIED" and entry["checks"][-1] == "egress_guard:refused"
    assert "mirror.example.org" in entry["reason"]
    assert not run.released and "ART118" in run.verdict.codes


@pytest.mark.parametrize("mode", ["unbrokered", "socket"])
def test_a_connection_around_the_broker_is_refused_and_recorded(probe, canned, mode):
    network = canned({ENTRY_URL: ENTRY})
    run, state = probe(mode)
    assert network.opened == []
    (entry,) = run.operations
    assert entry["operation"] == "unbrokered" and entry["declared"] is False
    assert entry["status"] == "DENIED" and entry["checks"] == ["egress_guard:refused"]
    assert "outside the operation broker" in entry["reason"]
    assert run.artifact.provenance["outcome"] == "carried on after PermissionError"
    assert len(_operation_events(state)) == 1
    assert not run.released and "ART118" in run.verdict.codes


def test_outside_a_governed_run_an_operation_runs_as_before(canned):
    network = canned({ENTRY_URL: ENTRY})
    assert current_broker() is None
    assert operation(RCSB_ENTRY, fetch_bytes, url=ENTRY_URL, timeout=5) == ENTRY
    assert urllib.request.urlopen(ENTRY_URL).read() == ENTRY        # noqa: S310
    assert network.opened == [ENTRY_URL, ENTRY_URL]


# ======================================================== the offline P0 skills

P0_RUNS = {"normalize-tcm-entities": {"names": ["黄芪"]},
           "retrieve-tcm-evidence": {"subject": "附子"},
           "analyze-tcm-network-pharmacology": {"formula_name": "桂枝汤"},
           "assess-tcm-safety": {"subject": "附子"}}


def test_the_offline_p0_skills_are_still_released(tmp_path):
    """Pinned against a lockfile written from the skills as they are on disk, so the test
    holds the operation path to the P0 skills whatever the shipped pins say."""
    lock = _pin(tmp_path / "skills.lock.yaml", *(P0 / s for s in P0_RUNS))
    for skill_id, arguments in P0_RUNS.items():
        state = tmp_path / skill_id / "psh"
        run = run_governed(skill_id, arguments, skill_dir=P0, lockfile=lock,
                           state_dir=state, output_dir=tmp_path / skill_id / "out")
        assert run.released, (skill_id, run.verdict.as_dict())
        assert run.operations == () and run.artifact.policy_id == "default"
        assert run.artifact.provenance["governed"]["operations"] == []
        assert _operation_events(state) == []


# ======================================================== the call sites

@pytest.fixture
def broker_for(tmp_path):
    """A broker for a skill manifest, under a fresh kernel with the policy a governed run
    of that skill gets."""
    from psh import PSHConfig, TrustedKernel

    from bioagent.governed import _run_policy

    kernels = []

    def make(skill_dir: Path) -> OperationBroker:
        spec = load_skill_dir(skill_dir).spec
        config = PSHConfig(state_dir=tmp_path / spec.id).ensure_dirs()
        kernel = TrustedKernel(config, policy=_run_policy(config, spec))
        kernels.append(kernel)
        kernel.audit("bioscience_skill_run_started", run_id=spec.id)
        return OperationBroker(spec, kernel, run_id=spec.id, anchor=kernel.events.head_hash)
    yield make
    for kernel in kernels:
        kernel.close()


def test_the_structure_pipelines_calls_go_through_the_broker(broker_for, canned):
    from bioagent.structure import predict as P

    network = canned({P.ESM_ATLAS: (FIX / "ubq_esmfold.pdb").read_bytes(),
                      ENTRY_URL: (FIX / "1ubq.pdb").read_bytes()})
    broker = broker_for(MOLECULAR / "predict-protein-structure")
    with broker.governing():
        prediction = P.predict("ubq", UBQ, method="esmatlas", allow_remote=True)
        P.fetch_reference("PDB:1UBQ", allow_remote=True)
        # Refused on the declaration, before a process is started or a weight downloaded.
        with pytest.raises(OperationRefused, match="api.colabfold.com"):
            operation(COLABFOLD_BATCH, P.colabfold_batch, executable="colabfold_batch",
                      fasta="ubq.fasta", out_dir="out")
        with pytest.raises(OperationRefused, match="huggingface.co"):
            operation(ESMFOLD_WEIGHTS, P.load_esmfold, model_id=P.ESMFOLD_MODEL)
    assert prediction.remote and prediction.sequence == UBQ
    assert network.opened == [P.ESM_ATLAS, ENTRY_URL]
    assert [(e.operation, e.status, e.declared) for e in broker.entries] == [
        (ESMATLAS_FOLD, "SUCCEEDED", True), (RCSB_ENTRY, "SUCCEEDED", True),
        (COLABFOLD_BATCH, "DENIED", False), (ESMFOLD_WEIGHTS, "DENIED", False)]
    assert all(e.recorded for e in broker.entries)


def test_a_declared_protein_sequence_reaches_the_public_fold_service(broker_for, canned):
    """Before, PSH's classifier floored a sequence of about a hundred residues at
    SENSITIVE ("uninspectable high-entropy content") and its egress gate refused it at a
    public service, so a governed fold of lysozyme was refused. The fold component now
    declares its ``sequence`` a protein sequence, and PSH labels a value that validates
    against the residue alphabet as research data."""
    from bioagent.structure import predict as P

    model = (FIX / "ubq_esmfold.pdb").read_bytes()
    network = canned({P.ESM_ATLAS: model})
    broker = broker_for(MOLECULAR / "predict-protein-structure")
    with broker.governing():
        raw = operation(ESMATLAS_FOLD, P.esmatlas_fold, sequence=LYSOZYME, timeout=5)
    assert raw == model and network.opened == [P.ESM_ATLAS]
    (entry,) = broker.entries
    assert (entry.status, entry.declared, entry.recorded) == ("SUCCEEDED", True, True)
    assert entry.checks == ("skill_manifest:passed", "psh_broker:passed",
                            "bioscience_resolver:passed", "bioscience_policy:passed")


@pytest.mark.parametrize("value", [
    "sk-live-Zq3vT9wXk2LmP8rB5nD7cF4h",                       # a key
    base64.b64encode(b"Patient Alice Smith MRN 04851923").decode(),  # an encoded note
    LYSOZYME.lower(),                                          # not the declared alphabet
])
def test_what_is_not_a_sequence_is_refused_in_the_sequence_field(broker_for, canned, value):
    """The declaration is not an exemption: a value that does not validate against the
    alphabet is refused by PSH's gate before anything is sent, and the record names the
    field, not the value."""
    from bioagent.structure import predict as P

    network = canned({P.ESM_ATLAS: b"never sent"})
    broker = broker_for(MOLECULAR / "predict-protein-structure")
    with broker.governing(), pytest.raises(OperationRefused) as refused:
        operation(ESMATLAS_FOLD, P.esmatlas_fold, sequence=value, timeout=5)
    assert network.opened == []
    entry = refused.value.evidence
    assert entry.status == "DENIED" and entry.declared and entry.recorded
    assert entry.checks == ("skill_manifest:passed", "psh_broker:refused")
    assert "sequence is declared protein-sequence" in entry.reason
    assert value not in entry.reason and value[8:20] not in entry.reason


def test_the_docking_pipelines_calls_go_through_the_broker(broker_for, canned):
    from bioagent.docking import prep

    ccd = json.dumps({"rcsb_chem_comp_descriptor": {"smiles": "NC(=N)c1ccccc1"}}).encode()
    canned({CHEMCOMP_URL: ccd, ENTRY_URL: ENTRY})
    broker = broker_for(MOLECULAR / "dock-ligands")
    with broker.governing():
        assert prep.ccd_smiles("BEN") == "NC(=N)c1ccccc1"
        assert prep.fetch_pdb("1ubq") == ENTRY.decode()
    assert [(e.operation, e.status) for e in broker.entries] == [
        (RCSB_CHEMCOMP, "SUCCEEDED"), (RCSB_ENTRY, "SUCCEEDED")]


def test_the_broker_refuses_a_call_its_component_does_not_describe(broker_for, canned):
    canned({})
    broker = broker_for(MOLECULAR / "predict-protein-structure")
    with broker.governing():
        with pytest.raises(OperationRefused, match="no operation 'made.up'"):
            operation("made.up", fetch_bytes, url=ENTRY_URL)
        with pytest.raises(OperationRefused, match="the call would run"):
            operation(RCSB_ENTRY, hashlib.sha256, url=ENTRY_URL)
        with pytest.raises(OperationRefused, match="files.example.org"):
            operation(RCSB_ENTRY, fetch_bytes, url="https://files.example.org/x.pdb")
        with pytest.raises(OperationRefused, match="data.rcsb.org"):
            operation(ALPHAFOLD_MODEL, fetch_bytes, url=CHEMCOMP_URL)
    assert [e.status for e in broker.entries] == ["DENIED"] * 4


# ======================================================== the ledger in the artifact

def _ledgered(*entries, anchor="a" * 64):
    from bioagent.skills.p0.entities import normalize_tcm_entities
    base = normalize_tcm_entities(["黄芪"])
    return dc.replace(base, provenance={**base.provenance, "governed": {
        "run_anchor": anchor, "operations": [dict(e) for e in entries]}})


def _entry(**kw):
    entry = {"seq": 1, "operation": RCSB_ENTRY, "implementation": "x", "declared": True,
             "hosts": ["files.rcsb.org"], "destinations": ["PUBLIC_REMOTE"],
             "label_ceiling": "RESEARCH_DEIDENTIFIED", "checks": [], "status": "SUCCEEDED",
             "reason": "", "arguments_sha256": "", "output_sha256": "", **kw}
    return {**entry, "evidence_sha256": operation_digest(entry),
            "recorded": kw.get("recorded", True)}


@pytest.mark.parametrize("entry,blocked", [
    (_entry(), False),
    (_entry(status="DEGRADED", reason="truncated"), False),
    (_entry(status="FAILED", reason="HTTP 500"), True),
    (_entry(status="DENIED", reason="undeclared"), True),
    (_entry(status="TIMEOUT"), True),
    (_entry(recorded=False), True),
])
def test_the_validator_reads_the_operation_ledger(entry, blocked):
    verdict = validate_artifact(_ledgered(entry), content_store=default_store)
    assert ("ART118" in verdict.codes) is blocked
    assert verdict.publishable is not blocked


def test_an_artifact_without_a_ledger_is_judged_as_before():
    from bioagent.skills.p0.entities import normalize_tcm_entities
    verdict = validate_artifact(normalize_tcm_entities(["黄芪"]), content_store=default_store)
    assert verdict.publishable and "ART118" not in verdict.codes


def test_the_attestor_holds_the_ledger_to_the_chain(tmp_path):
    """The run records two operations; an artifact released on the strength of one of them
    is not attested, nor is one that claims an operation the chain never saw."""
    from psh import PSHConfig, TrustedKernel

    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path).ensure_dirs())
    try:
        kernel.audit("bioscience_skill_run_started", run_id="t")
        anchor = kernel.events.head_hash
        first, second = _entry(seq=1), _entry(seq=2, operation=RCSB_CHEMCOMP)
        for entry in (first, second):
            kernel.audit("bioscience_operation", run_id="t",
                         detail={**{k: v for k, v in entry.items() if k != "recorded"},
                                 "run_anchor": anchor})

        def release(*entries):
            artifact = dc.replace(_ledgered(*entries, anchor=anchor), policy_id="p",
                                  audit_head=kernel.events.head_hash)
            kernel.audit("bioscience_artifact_attested", run_id="t",
                         detail={"artifact_digest": artifact.digest,
                                 "audit_head": artifact.audit_head, "policy_id": "p"})
            return AuditChainAttestor(kernel.events).attest(artifact)

        assert release(first, second) == (True, "")
        ok, why = release(first)
        assert not ok and "leaves out" in why
        ok, why = release(first, second, _entry(seq=3, operation=ALPHAFOLD_MODEL))
        assert not ok and "operation 3" in why
    finally:
        kernel.close()


# ======================================================== skill programs

def _contract(tool: str = "api.lookup"):
    from bioagent.providers.skills import SkillContract
    return SkillContract(id="tcm.remote-lookup", version="0.1.0", tools=(tool,),
                         max_claim_kind="mechanism_hypothesis",
                         steps={"lookup": {"tool": tool, "design": "in_silico"}})


def _http_component():
    from bioagent.psh import bridge_manifest
    from bioagent.runtime.component import (ComponentManifest, LicenseSpec, Permissions,
                                            RuntimeSpec)
    return bridge_manifest(ComponentManifest(
        id="public.connector.lookup", kind="connector", name="a public lookup service",
        runtime=RuntimeSpec(backend="http", server="https://api.example.org"),
        permissions=Permissions(network=("api.example.org",)),
        license=LicenseSpec(spdx="CC0-1.0", integration_mode="federated")))


def _scope():
    from bioagent.psh.skill_program import ClaimScope
    return ClaimScope("human proteins (in silico)", "葛根芩连汤", "pathways")


def test_a_program_step_reaches_what_its_http_component_reaches():
    from psh.labels import Destination, Sensitivity
    from psh.policy import PolicySnapshot
    from psh.workflow import Effect, ScientificCompiler

    from bioagent.psh.skill_program import skill_program

    policy = PolicySnapshot(profile_id="remote", require_claim_support=False,
                            allowed_destinations=(Destination.LOCAL_COMPUTE,
                                                  Destination.LOCAL_MODEL,
                                                  Destination.PUBLIC_REMOTE))
    program = skill_program(_contract(), _scope(),
                            components={"api.lookup": _http_component()},
                            envelope=policy.envelope())
    lookup = next(t for t in program.plan.tasks if t.task_id == "lookup")
    assert lookup.destinations == (Destination.PUBLIC_REMOTE,)
    assert lookup.component_id == "public.connector.lookup"
    assert lookup.max_label == Sensitivity.RESEARCH_DEIDENTIFIED     # not PUBLIC, not PHI
    assert program.contracts["lookup"].effects == (Effect.PUBLIC_REMOTE,)
    compiled = ScientificCompiler().compile(program, policy.envelope(), policy=policy)
    assert compiled.validated.order == ("lookup", "claim")


def test_a_program_step_the_run_does_not_authorise_is_refused():
    from psh.policy import PolicySnapshot

    from bioagent.psh.skill_program import SkillProgramError, skill_program

    local_only = PolicySnapshot(profile_id="local", require_claim_support=False).envelope()
    with pytest.raises(SkillProgramError) as refused:
        skill_program(_contract(), _scope(), components={"api.lookup": _http_component()},
                      envelope=local_only)
    message = str(refused.value)
    assert "'lookup'" in message and "'api.lookup'" in message
    assert "PUBLIC_REMOTE" in message and "LOCAL_COMPUTE" in message


def test_a_program_without_a_registry_or_with_an_unknown_tool_is_refused():
    from psh.policy import PolicySnapshot

    from bioagent.psh.skill_program import SkillProgramError, skill_program

    envelope = PolicySnapshot(profile_id="local", require_claim_support=False).envelope()
    with pytest.raises(SkillProgramError, match="no component registry"):
        skill_program(_contract(), _scope(), envelope=envelope)
    with pytest.raises(SkillProgramError, match="no run envelope"):
        skill_program(_contract(), _scope(), components={"api.lookup": _http_component()})
    with pytest.raises(SkillProgramError, match="'api.lookup', which is not an admitted"):
        skill_program(_contract(), _scope(), components={"other": _http_component()},
                      envelope=envelope)


# ======================================================== against the real services

@pytest.mark.integration
def test_a_live_download_is_brokered_and_released(probe):
    run, _ = probe("declared")
    (entry,) = run.operations
    assert entry["status"] == "SUCCEEDED", entry
    assert entry["destinations"] == ["LOCAL_COMPUTE", "PUBLIC_REMOTE"]
    assert run.released, run.verdict.as_dict()


@pytest.mark.integration
def test_a_live_esm_atlas_fold_is_brokered(tmp_path):
    run = run_governed("predict-protein-structure",
                       {"sequences": UBQ, "method": "esmatlas", "allow_remote": True,
                        "out_dir": str(tmp_path / "run")},
                       skill_dir=MOLECULAR, state_dir=tmp_path / "psh",
                       output_dir=tmp_path / "out", allow_unpinned=True)
    (entry,) = run.operations
    assert entry["operation"] == ESMATLAS_FOLD and entry["status"] == "SUCCEEDED", entry
    assert entry["hosts"] == ["api.esmatlas.com"]
    assert "PUBLIC_REMOTE" in entry["destinations"]
    assert run.verdict.publishable and not run.released             # a development run
    assert run.artifact.provenance["governed"]["release_refused"][0].startswith("unpinned")


@pytest.mark.integration
def test_a_live_esm_atlas_fold_of_lysozyme_is_brokered(tmp_path):
    """129 residues: refused as uninspectable content until the fold declared its field a
    protein sequence; now sent, folded and recorded like ubiquitin."""
    run = run_governed("predict-protein-structure",
                       {"sequences": LYSOZYME, "method": "esmatlas", "allow_remote": True,
                        "out_dir": str(tmp_path / "run")},
                       skill_dir=MOLECULAR, state_dir=tmp_path / "psh",
                       output_dir=tmp_path / "out", allow_unpinned=True)
    (entry,) = run.operations
    assert entry["operation"] == ESMATLAS_FOLD and entry["status"] == "SUCCEEDED", entry
    assert entry["checks"] == ["skill_manifest:passed", "psh_broker:passed",
                               "bioscience_resolver:passed", "bioscience_policy:passed"]
    assert entry["recorded"] and entry["output_sha256"]
    assert run.verdict.publishable and not run.released             # a development run
