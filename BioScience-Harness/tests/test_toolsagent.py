"""SciToolAgent's ToolsAgent service through the adapter, against a local stub of its shape.

The stub serves ``POST /run-func`` as ToolsAgent's ``main.py`` declares it (read on
2026-10-07): ``func_name`` and ``func_args`` as query parameters, a required JSON body
``{"file_path_list": [...]}``, 422 when the request has another shape, 400 for a
ValueError (an uninstalled tool module among them), 500 for any other exception (an
unknown ``func_name`` among them), and ``{"status_code": 200, "result": ...}`` otherwise.
Its functions return what the ToolsAgent functions of the same names return, errors
included. One test serves the same signature with FastAPI itself, when it is installed.

The last section calls a real deployment, named by ``TOOLSAGENT_URL``, with
``TOOLSAGENT_CATEGORIES`` listing the tool categories it has installed (CI deploys the
Chemical category from the reviewed commit). It skips without one, and fails instead
under BIOAGENT_REQUIRE_TOOLS.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

from bioagent.backends.base import BackendRegistry
from bioagent.backends.http import HTTPBackend
from bioagent.backends.jobs import CancelGrant, JobController, JobState
from bioagent.backends.toolsagent import (DOUBLE_SEQUENCE_GLOBAL_ALIGNMENT, MOL_SIMILARITY,
                                          SMILES_TO_INCHI, SMILES_TO_WEIGHT, ServerFile,
                                          ToolsAgentClient, ToolsAgentJobs,
                                          ToolsAgentRequestError, ToolsAgentTool)
from bioagent.policy import PROFILES, PermissionProfile, PolicyKernel
from bioagent.runtime.agentspec import AgentSpec, Runtime
from bioagent.runtime.events import EventLog, EventType
from bioagent.runtime.registry import ComponentRegistry
from bioagent.status import ExecutionStatus
from omics_world import need_module

WEIGHTS = {"CCO": 46.041864814, "c1ccccc1": 78.046950192}
INCHIS = {"CCO": "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"}
SLOW_ECHO = ToolsAgentTool("SlowEcho", ("text",), accept=r"^echo:(.*)$")
WRITE_MODEL = ToolsAgentTool("WriteModel", ("name",), accept=r"^(\S+\.pdb)$",
                             output="server_file")
READ_FILES = ToolsAgentTool("ReadFiles", ("mode",), accept=r"^read \d+ files",
                            takes_files=True)


def _weight(smiles, files, workdir):
    if smiles not in WEIGHTS:
        return "Invalid SMILES string"
    return (f"\n### Molecular Weight Calculation\n\n#### Input Molecule\n\n- **SMILES**: "
            f"`{smiles}`\n\n#### Molecular Weight\n\n- **Weight**: "
            f"`{WEIGHTS[smiles]:.2f} g/mol`\n\n")


def _similarity(pair, files, workdir):
    parts = pair.split(".")
    if len(parts) != 2:
        return "Input error, please input two smiles strings separated by '.'"
    if parts[0] == parts[1]:
        return "Error: Input Molecules Are Identical"
    return ("\n### Molecule Similarity\n\n#### Similarity Result\n\n"
            "- **Tanimoto Similarity**: `0.1667`\n- **Interpretation**: not similar\n")


def _inchi(smiles, files, workdir):
    cleaned = smiles.replace("\n", "").replace(" ", "")
    if cleaned not in INCHIS:
        return None                            # MolToInchi(None) raises; the tool logs it
    return f"\n**SMILES to InChI**\nSMILES={cleaned}\n**Result:**\n{INCHIS[cleaned]}\n"


def _write_model(name, files, workdir):
    (Path(workdir) / f"{name}.pdb").write_text("ATOM      1  CA  GLY A   1       0.000"
                                               "   0.000   0.000  1.00 90.00           C\n")
    return f"{name}.pdb"


def _raise(arg, files, workdir):
    raise RuntimeError("the tool crashed")


FUNCTIONS = {
    "SMILESToWeight": _weight, "MolSimilarity": _similarity, "SMILESToInChI": _inchi,
    "DoubleSequenceGlobalAlignment": lambda pair, f, w: (
        "\n***Alignment of two sequences***\nInput sequences: ...\n"),
    "BrokenTool": lambda arg, f, w: None,
    "SlowEcho": lambda arg, f, w: (time.sleep(0.4), f"echo:{arg}")[1],
    "WriteModel": _write_model,
    "ReadFiles": lambda mode, files, w: f"read {len(files)} files: "
                                        + ",".join(Path(p).name for p in files),
    "ReadFilesSlowly": lambda mode, files, w: (time.sleep(0.3), f"read {len(files)} files")[1],
    "Raise": _raise,
}
NOT_INSTALLED = {"PredictProteinSolubility"}


class _ToolsAgent(http.server.BaseHTTPRequestHandler):
    def _send(self, code: int, doc=None, text: str | None = None) -> None:
        body = text.encode() if text is not None else json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8" if text is not None
                         else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - http.server API
        split = urllib.parse.urlsplit(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(split.query,
                                                          keep_blank_values=True).items()}
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.seen.append({"path": split.path, "query": query, "body": raw,
                                 "content_type": self.headers.get("Content-Type")})
        if split.path != "/run-func":
            return self._send(404, {"detail": "Not Found"})
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        missing = [k for k in ("func_name", "func_args") if k not in query]
        if missing or not isinstance(body, dict) or "file_path_list" not in body:
            return self._send(422, {"detail": [{"type": "missing", "loc": ["query", m],
                                                "msg": "Field required"} for m in missing]
                                    or [{"type": "missing", "loc": ["body"]}]})
        name = query["func_name"]
        if name in NOT_INSTALLED:
            return self._send(400, {"detail": f"Module for {name} not found at "
                                              f"ToolsFuns.Biology.tool_name_dict."})
        if name not in FUNCTIONS:
            return self._send(500, text="Internal Server Error")      # KeyError
        started = time.monotonic()
        try:
            result = FUNCTIONS[name](query["func_args"], body["file_path_list"],
                                     self.server.workdir)
        except ValueError as exc:
            return self._send(400, {"detail": str(exc)})
        except Exception:  # noqa: BLE001 - what FastAPI answers for anything else
            return self._send(500, text="Internal Server Error")
        finally:
            self.server.spans.append((name, started, time.monotonic()))
        self._send(200, {"status_code": 200, "result": result})

    def log_message(self, *args):
        pass


@pytest.fixture
def toolsagent(monkeypatch, tmp_path):
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ToolsAgent)
    httpd.seen, httpd.spans, httpd.workdir = [], [], tmp_path / "service"
    httpd.workdir.mkdir()
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd
    httpd.shutdown()


def client(url: str, **kw) -> ToolsAgentClient:
    return ToolsAgentClient(url, http=HTTPBackend(max_retries=1, timeout_s=10,
                                                  rates={"127.0.0.1": 1000.0}), **kw)


# --------------------------------------------------------------- components
def test_each_function_is_a_component_declaring_where_its_data_goes():
    m = DOUBLE_SEQUENCE_GLOBAL_ALIGNMENT.manifest("http://10.0.0.5:60002/")
    assert m.id == "scitoolagent.tool.double_sequence_global_alignment"
    assert SMILES_TO_WEIGHT.component_id == "scitoolagent.tool.smiles_to_weight"
    assert m.validate() == [] and m.runtime.backend == "http"
    assert m.runtime.entrypoint == "run-func:DoubleSequenceGlobalAlignment"
    assert m.permissions.network == ("10.0.0.5", "www.novopro.cn"), \
        "the host it sends sequences on to is declared, so a profile can refuse it"
    assert m.inputs["arguments"] == ["sequence1", "sequence2"] and m.inputs["separator"] == "."
    assert "monoisotopic" in SMILES_TO_WEIGHT.manifest("http://h:1").description


def test_arguments_are_rendered_by_the_functions_rule_or_refused_unsent():
    request = MOL_SIMILARITY.render({"smiles1": "CCO", "smiles2": "c1ccccc1"})
    assert request["params"] == {"func_name": "MolSimilarity", "func_args": "CCO.c1ccccc1"}
    assert request["json_body"] == {"file_path_list": []} and request["method"] == "POST"
    with pytest.raises(ToolsAgentRequestError, match="contains '.', the separator"):
        MOL_SIMILARITY.render({"smiles1": "CC(=O)[O-].[Na+]", "smiles2": "CCO"})
    with pytest.raises(ToolsAgentRequestError, match=r"missing \['smiles2'\]; unknown"):
        MOL_SIMILARITY.render({"smiles1": "CCO", "smile2": "CCN"})
    with pytest.raises(ToolsAgentRequestError, match="smiles is empty"):
        SMILES_TO_WEIGHT.render({"smiles": "  "})
    with pytest.raises(ToolsAgentRequestError, match="reads no files"):
        SMILES_TO_WEIGHT.render({"smiles": "CCO"}, [ServerFile("/data/a.sdf")])
    with pytest.raises(ValueError, match="need the separator"):
        ToolsAgentTool("Pair", ("a", "b"), accept=".+")
    with pytest.raises(ValueError, match="accept needs a group"):
        ToolsAgentTool("Weight", ("smiles",), accept="g/mol", output="number")


def test_files_are_references_on_the_service_host_never_uploads():
    ok = READ_FILES.render({"mode": "all"}, [ServerFile("/srv/data/a.pdb"),
                                            ServerFile("/srv/data/b.pdb")])
    assert ok["json_body"] == {"file_path_list": ["/srv/data/a.pdb", "/srv/data/b.pdb"]}
    with pytest.raises(ToolsAgentRequestError, match="keeps only its extension"):
        READ_FILES.render({"mode": "all"}, [ServerFile("/srv/data/a.sdf"),
                                            ServerFile("/srv/data/b.pdb")])
    with pytest.raises(ToolsAgentRequestError, match="last file's directory"):
        READ_FILES.render({"mode": "all"}, [ServerFile("/srv/x/a.pdb"),
                                            ServerFile("/srv/data/b.pdb")])
    with pytest.raises(ToolsAgentRequestError, match="not an absolute path"):
        READ_FILES.render({"mode": "all"}, [ServerFile("a.pdb")])
    with pytest.raises(ToolsAgentRequestError, match="name at least one"):
        READ_FILES.render({"mode": "all"})


# ------------------------------------------------------------------- calls
def test_the_request_on_the_wire_is_the_one_toolsagent_serves(toolsagent):
    url, httpd = toolsagent
    result = client(url).call(SMILES_TO_WEIGHT, smiles="CCO")
    assert result.status is ExecutionStatus.SUCCEEDED, result.error
    assert result.value["value"] == 46.04 and "monoisotopic" in result.value["note"]
    seen = httpd.seen[-1]
    assert seen["path"] == "/run-func"
    assert seen["query"] == {"func_name": "SMILESToWeight", "func_args": "CCO"}
    assert json.loads(seen["body"]) == {"file_path_list": []}
    assert seen["content_type"] == "application/json"
    files = client(url).call(READ_FILES, mode="all", files=[ServerFile("/srv/d/a.pdb"),
                                                           ServerFile("/srv/d/b.pdb")])
    assert files.status is ExecutionStatus.SUCCEEDED
    assert json.loads(httpd.seen[-1]["body"])["file_path_list"] == ["/srv/d/a.pdb",
                                                                    "/srv/d/b.pdb"]


def test_a_string_result_is_the_value_its_shape_names(toolsagent):
    url, _ = toolsagent
    result = client(url).call(SMILES_TO_INCHI, smiles="CCO")
    assert result.status is ExecutionStatus.SUCCEEDED, result.error
    assert result.value["value"] == "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"
    assert "not ChemSpider" in result.value["note"]
    invalid = client(url).call(SMILES_TO_INCHI, smiles="C1CC")
    assert invalid.status is ExecutionStatus.FAILED and "returned no result" in invalid.error


@pytest.mark.parametrize("tool,arguments,words", [
    (SMILES_TO_WEIGHT, {"smiles": "C1CC"}, "'Invalid SMILES string'"),
    (MOL_SIMILARITY, {"smiles1": "CCO", "smiles2": "CCO"},
     "'Error: Input Molecules Are Identical'"),
    (ToolsAgentTool("BrokenTool", ("x",), accept=".+"), {"x": "1"},
     "return None when they catch their own exception"),
])
def test_an_error_returned_as_an_answer_is_a_failure(toolsagent, tool, arguments, words):
    url, _ = toolsagent
    result = client(url).call(tool, **arguments)
    assert result.status is ExecutionStatus.FAILED and result.executed
    assert words in result.error


def test_what_the_service_did_maps_to_what_happened(toolsagent):
    url, httpd = toolsagent
    absent = client(url).call(ToolsAgentTool("PredictProteinSolubility", ("sequence",),
                                             accept=".+"), sequence="MKV")
    assert absent.status is ExecutionStatus.UNAVAILABLE
    assert "the service has no PredictProteinSolubility" in absent.error
    raised = client(url).call(ToolsAgentTool("Raise", ("x",), accept=".+"), x="1")
    assert raised.status is ExecutionStatus.FAILED and "HTTP 500" in raised.error
    unknown = client(url).call(ToolsAgentTool("NoSuchFunction", ("x",), accept=".+"), x="1")
    assert unknown.status is ExecutionStatus.FAILED
    assert len([s for s in httpd.seen if s["query"].get("func_name") == "Raise"]) == 1, \
        "a 500 is the tool raising: it is not sent again"
    shape = HTTPBackend(max_retries=1, rates={"127.0.0.1": 1000.0}).invoke(
        SMILES_TO_WEIGHT.manifest(url), path="run-func", method="POST",
        params={"func_name": "SMILESToWeight", "func_args": "CCO"}, use_cache=False)
    from bioagent.backends.toolsagent import interpret

    refused = interpret(SMILES_TO_WEIGHT, shape)
    assert refused.status is ExecutionStatus.FAILED and "request's shape (HTTP 422)" in \
        refused.error
    down = client("http://127.0.0.1:9").call(SMILES_TO_WEIGHT, smiles="CCO")
    assert down.status is ExecutionStatus.UNAVAILABLE and not down.executed


def test_a_file_the_tool_writes_is_verified_only_where_it_can_be_read(toolsagent):
    url, httpd = toolsagent
    unseen = client(url).call(WRITE_MODEL, name="m1")
    assert unseen.status is ExecutionStatus.DEGRADED
    assert unseen.value["server_file"] == "m1.pdb"
    assert "neither digested nor validated" in unseen.error
    shared = client(url, shared_dirs={".": httpd.workdir}).call(WRITE_MODEL, name="m2")
    assert shared.status is ExecutionStatus.SUCCEEDED
    assert shared.value["sha256"] and shared.value["local_path"] == str(httpd.workdir /
                                                                        "m2.pdb")


def test_a_call_that_sends_files_never_overlaps_another_call_from_this_client(toolsagent):
    """ToolsAgent keeps each request's file list in a process-wide Config(): two calls in
    flight at once can hand a tool the other call's files."""
    url, httpd = toolsagent
    agent = client(url)
    reader = ToolsAgentTool("ReadFilesSlowly", ("mode",), accept=r"^read \d+ files",
                            takes_files=True)
    calls = [threading.Thread(target=agent.call, args=(SLOW_ECHO,), kwargs={"text": "a"}),
             threading.Thread(target=agent.call, args=(reader,),
                              kwargs={"mode": "all", "files": [ServerFile("/d/a.pdb")]}),
             threading.Thread(target=agent.call, args=(SLOW_ECHO,), kwargs={"text": "b"})]
    for thread in calls:
        thread.start()
        time.sleep(0.05)
    for thread in calls:
        thread.join(timeout=15)
    (start, end), = [(s, e) for name, s, e in httpd.spans if name == "ReadFilesSlowly"]
    others = [(s, e) for name, s, e in httpd.spans if name == "SlowEcho"]
    assert len(others) == 2
    assert all(e <= start or s >= end for s, e in others), httpd.spans


# --------------------------------------------------------------- long calls
def test_a_long_call_runs_as_a_job_and_cancelling_it_stops_nothing(toolsagent, tmp_path):
    url, _ = toolsagent
    jobs = ToolsAgentJobs(client(url), [SLOW_ECHO])
    ctl = JobController(jobs, collect_dir=tmp_path)
    ref = ctl.submit(ToolsAgentJobs.spec(SLOW_ECHO, text="hi"))
    assert ctl.status(ref).state is JobState.RUNNING
    assert ctl.collect(ref).status is ExecutionStatus.RUNNING, "submitted is not done"
    cancel = ctl.cancel(ref, CancelGrant(ref.job_id, "tests", "no longer needed"))
    assert cancel.status is ExecutionStatus.RUNNING
    assert "offers no cancellation" in cancel.detail and "nothing was stopped" in cancel.detail
    assert ctl.wait(ref, timeout_s=10, poll_s=0.02).state is JobState.COMPLETED
    outcome = ctl.collect(ref)
    assert outcome.status is ExecutionStatus.SUCCEEDED
    record = json.loads(outcome.artefacts["result"].path.read_text())
    assert record["status"] == "SUCCEEDED" and record["value"]["text"] == "echo:hi"
    with pytest.raises(ToolsAgentRequestError):
        ToolsAgentJobs.spec(MOL_SIMILARITY, smiles1="CCO", smiles2="C.C")


def test_an_unreadable_output_file_fails_the_jobs_collection(toolsagent, tmp_path):
    url, _ = toolsagent
    ctl = JobController(ToolsAgentJobs(client(url), [WRITE_MODEL]), collect_dir=tmp_path)
    outcome = ctl.run(ToolsAgentJobs.spec(WRITE_MODEL, name="m3"), timeout_s=10,
                      poll_s=0.02)
    assert outcome.status is ExecutionStatus.FAILED
    assert "no shared directory makes it readable here" in outcome.error


def test_a_call_from_a_process_that_has_ended_is_lost(toolsagent, tmp_path):
    url, _ = toolsagent
    first = JobController(ToolsAgentJobs(client(url), [SLOW_ECHO]))
    ref = first.submit(ToolsAgentJobs.spec(SLOW_ECHO, text="x"))
    restarted = JobController(ToolsAgentJobs(client(url), [SLOW_ECHO]))
    status = restarted.status(ref)
    assert status.state is JobState.LOST and "keeps no job records" in status.detail


# ---------------------------------------------------------------- governed
def test_a_governed_call_is_ruled_on_and_recorded(toolsagent):
    url, httpd = toolsagent
    manifest = SMILES_TO_WEIGHT.manifest(url)
    local = PermissionProfile(name="local-tools", allow_network=True,
                              allowed_hosts=frozenset({"127.0.0.1"}))
    runtime = Runtime(ComponentRegistry([manifest]), BackendRegistry(
        [HTTPBackend(max_retries=1, rates={"127.0.0.1": 1000.0})]),
        kernel=PolicyKernel(profiles={**PROFILES, "local-tools": local}))
    events = EventLog()
    allowed = ToolsAgentClient(url, runtime=runtime, events=events,
                               spec=AgentSpec(name="t", permission_profile="local-tools"))
    result = allowed.call(SMILES_TO_WEIGHT, smiles="c1ccccc1")
    assert result.status is ExecutionStatus.SUCCEEDED and result.value["value"] == 78.05
    called = events.of_type(EventType.TOOL_CALLED)[-1]
    assert called.component_id == manifest.id
    assert called.inputs["params"] == {"func_name": "SMILESToWeight", "func_args": "c1ccccc1"}
    sent = len(httpd.seen)
    denied = ToolsAgentClient(url, runtime=runtime, spec=AgentSpec(name="t")).call(
        SMILES_TO_WEIGHT, smiles="CCO")
    assert denied.status is ExecutionStatus.DENIED and "127.0.0.1" in denied.error
    assert len(httpd.seen) == sent, "a denied call never reaches the service"


# --------------------------------------------------- ToolsAgent's own signature
def test_fastapi_accepts_the_request_with_toolsagents_signature(monkeypatch):
    fastapi = need_module("fastapi")
    uvicorn = need_module("uvicorn")
    for var in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    app = fastapi.FastAPI()

    # The signature of ToolsAgent's main.run_func, verbatim; the body is the stub's.
    @app.post("/run-func")
    async def run_func(func_name: str, func_args: str,
                       file_path_list: list[str] = fastapi.Body(..., embed=True)):
        return {"status_code": 200, "result": _weight(func_args, file_path_list, None)}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        result = client(url).call(SMILES_TO_WEIGHT, smiles="CCO")
        assert result.status is ExecutionStatus.SUCCEEDED, result.error
        assert result.value["value"] == 46.04
        # The README's shape, every field in the JSON body, is not what this signature
        # reads: FastAPI refuses it, which is why the adapter follows the code.
        readme = HTTPBackend(max_retries=1, rates={"127.0.0.1": 1000.0}).invoke(
            SMILES_TO_WEIGHT.manifest(url), path="run-func", method="POST", use_cache=False,
            json_body={"func_name": "SMILESToWeight", "func_args": "CCO",
                       "file_path_list": []})
        assert readme.status is ExecutionStatus.FAILED and "HTTP 422" in readme.error
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# -------------------------------------------------------- a real deployment
def _deployment() -> tuple[str, set[str]]:
    url = os.environ.get("TOOLSAGENT_URL", "")
    if not url:
        why = "no ToolsAgent deployment: set TOOLSAGENT_URL (and TOOLSAGENT_CATEGORIES)"
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(why)
        pytest.skip(why)
    categories = {c.strip() for c in os.environ.get("TOOLSAGENT_CATEGORIES", "").split(",")}
    return url, categories - {""}


def _deployed(url: str) -> ToolsAgentClient:
    host = url.split("//", 1)[1].split("/", 1)[0].split(":")[0]
    return ToolsAgentClient(url, http=HTTPBackend(max_retries=1, timeout_s=60,
                                                  rates={host: 1000.0}))


def test_the_deployed_service_answers_the_chemical_functions_as_reviewed():
    url, categories = _deployment()
    if "Chemical" not in categories:
        pytest.skip("the deployment does not list the Chemical category")
    agent = _deployed(url)
    weight = agent.call(SMILES_TO_WEIGHT, smiles="CCO")
    assert weight.status is ExecutionStatus.SUCCEEDED, weight.error
    assert weight.value["value"] == 46.04, "RDKit's monoisotopic mass of ethanol, 46.0419"
    similar = agent.call(MOL_SIMILARITY, smiles1="CCO", smiles2="CCN")
    assert similar.status is ExecutionStatus.SUCCEEDED, similar.error
    assert 0 < similar.value["value"] < 1
    inchi = agent.call(SMILES_TO_INCHI, smiles="CCO")
    assert inchi.status is ExecutionStatus.SUCCEEDED, inchi.error
    assert inchi.value["value"] == "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"


def test_the_deployed_service_agrees_with_rdkit_here():
    url, categories = _deployment()
    if "Chemical" not in categories:
        pytest.skip("the deployment does not list the Chemical category")
    rdkit = need_module("rdkit")
    from rdkit import Chem, DataStructs
    from rdkit.Chem import rdFingerprintGenerator, rdMolDescriptors

    agent = _deployed(url)
    for smiles in ("c1ccccc1O", "CC(=O)Oc1ccccc1C(=O)O", "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"):
        mol = Chem.MolFromSmiles(smiles)
        weight = agent.call(SMILES_TO_WEIGHT, smiles=smiles)
        assert weight.value["value"] == round(rdMolDescriptors.CalcExactMolWt(mol), 2)
        inchi = agent.call(SMILES_TO_INCHI, smiles=smiles)
        assert inchi.value["value"] == Chem.MolToInchi(mol), rdkit.__version__
    one, two = "c1ccccc1O", "c1ccccc1N"
    # The service calls GetMorganFingerprintAsBitVect(mol, 2, nBits=2048); the generator
    # makes the same bits without that function's deprecation.
    morgan = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    fps = [morgan.GetFingerprint(Chem.MolFromSmiles(s)) for s in (one, two)]
    similar = agent.call(MOL_SIMILARITY, smiles1=one, smiles2=two)
    assert similar.value["value"] == round(DataStructs.TanimotoSimilarity(*fps), 4)


def test_the_deployed_services_errors_are_failures_not_results():
    url, categories = _deployment()
    if "Chemical" not in categories:
        pytest.skip("the deployment does not list the Chemical category")
    agent = _deployed(url)
    identical = agent.call(MOL_SIMILARITY, smiles1="CCO", smiles2="CCO")
    assert identical.status is ExecutionStatus.FAILED and "Identical" in identical.error
    invalid = agent.call(SMILES_TO_WEIGHT, smiles="C1CC")
    assert invalid.status is ExecutionStatus.FAILED and "Invalid SMILES" in invalid.error
    no_inchi = agent.call(SMILES_TO_INCHI, smiles="C1CC")
    assert no_inchi.status is ExecutionStatus.FAILED
    unknown = agent.call(ToolsAgentTool("NoSuchFunction", ("x",), accept=".+"), x="1")
    assert unknown.status is ExecutionStatus.FAILED and "HTTP 500" in unknown.error


def test_a_category_the_deployment_lacks_is_unavailable_not_failed():
    """Only asked of a deployment without Biology: with it installed, the alignment would
    send both sequences on to NovoPro, which a test does not do."""
    url, categories = _deployment()
    if "Biology" in categories:
        pytest.skip("the deployment has the Biology category")
    result = _deployed(url).call(DOUBLE_SEQUENCE_GLOBAL_ALIGNMENT, sequence1="MKTAYIAK",
                                 sequence2="MKTAHIAK")
    assert result.status is ExecutionStatus.UNAVAILABLE, result.error
    assert "the service has no DoubleSequenceGlobalAlignment" in result.error


def test_a_governed_call_to_the_deployment_is_ruled_on_and_recorded():
    url, categories = _deployment()
    if "Chemical" not in categories:
        pytest.skip("the deployment does not list the Chemical category")
    manifest = SMILES_TO_WEIGHT.manifest(url)
    host = manifest.permissions.network[0]
    local = PermissionProfile(name="toolsagent", allow_network=True,
                              allowed_hosts=frozenset({host}))
    runtime = Runtime(ComponentRegistry([manifest]), BackendRegistry(
        [HTTPBackend(max_retries=1, rates={host: 1000.0})]),
        kernel=PolicyKernel(profiles={**PROFILES, "toolsagent": local}))
    events = EventLog()
    result = ToolsAgentClient(url, runtime=runtime, events=events,
                              spec=AgentSpec(name="t", permission_profile="toolsagent")
                              ).call(SMILES_TO_WEIGHT, smiles="c1ccccc1")
    assert result.status is ExecutionStatus.SUCCEEDED and result.value["value"] == 78.05
    assert events.of_type(EventType.TOOL_CALLED)[-1].component_id == manifest.id
    denied = ToolsAgentClient(url, runtime=runtime, spec=AgentSpec(name="t")).call(
        SMILES_TO_WEIGHT, smiles="CCO")
    assert denied.status is ExecutionStatus.DENIED
