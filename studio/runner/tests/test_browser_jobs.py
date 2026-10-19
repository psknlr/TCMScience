"""tcmstudio.browser_jobs, run natively: the Python a browser job calls, against the commands
the runner runs for the same work.

A browser job's stages call these functions in Pyodide; here they run in CPython, so what
is checked is that they are the runner's commands with JSON around them: the research run
writes the bytes ``bioagent research`` writes, the snapshot build gives the ids
``bioagent snapshots build --offline`` gives, the verification is the runner's, and every
failure comes back as data. The planted worlds (bioagent.research.planted) and the parser
fixtures (BioScience-Harness/tests) are fixtures, not measurements. Pyodide itself is run
by studio/web/test/runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
from dataclasses import replace
from pathlib import Path

import pytest

from tcmstudio import browser_jobs
from tcmstudio.browser_jobs import call

HARNESS_TESTS = Path(__file__).resolve().parents[3] / "BioScience-Harness" / "tests"
QUESTION = "葛根芩连汤的实测靶点集中在哪些通路？"
TABLES = ("compounds.tsv", "targets.tsv", "disease_targets.tsv", "enrichment.tsv",
          "network.tsv")
SAME_BYTES = (*TABLES, "limitations.md", "report.html", "provenance.json")


@pytest.fixture(autouse=True)
def _no_formula_table(monkeypatch):
    # the questions here name a hand-checked formula; nothing should read the 14 MB table
    monkeypatch.setenv("BIOAGENT_FORMULA_TABLE", "none")


def _ok(text: str) -> object:
    doc = json.loads(text)
    assert doc["ok"] is True, doc
    return doc["result"]


def _error(text: str) -> dict:
    doc = json.loads(text)
    assert doc["ok"] is False and set(doc["error"]) == {"type", "message"}, doc
    return doc["error"]


def _env() -> dict[str, str]:
    """This interpreter's import path, for a child: the packages may come from it."""
    return {**os.environ, "BIOAGENT_FORMULA_TABLE": "none",
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}


def _research_args(root: Path, ledger: Path, tmp: Path, tag: str) -> dict:
    return {"question": QUESTION, "permutations": 200, "snapshot_root": str(root),
            "ledger": str(ledger), "state_dir": str(tmp / f"state-{tag}"),
            "out_dir": str(tmp / f"out-{tag}")}


def _cli(root: Path, ledger: Path, tmp: Path, tag: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "bioagent.cli", "research", QUESTION, "--snapshots", str(root),
         "--ledger", str(ledger), "--state-dir", str(tmp / f"state-{tag}"),
         "--out", str(tmp / f"out-{tag}"), "--permutations", "200"],
        capture_output=True, text=True, timeout=600, env=_env(), cwd=tmp)


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    """The selective planted world, researched by ``bioagent research`` (a child process)
    and by ``browser_jobs.run_research`` natively and as the browser runs it (in process)."""
    from bioagent.research.planted import build_world
    from bioagent.sources.ledger import SnapshotLedger

    tmp = tmp_path_factory.mktemp("planted")
    old = os.environ.get("BIOAGENT_FORMULA_TABLE")
    os.environ["BIOAGENT_FORMULA_TABLE"] = "none"
    try:
        ledger = SnapshotLedger(tmp / "w" / "ledger.jsonl")
        build_world("selective", tmp / "w" / "snap", ledger=ledger)
        root = tmp / "w" / "snap"
        cli = _cli(root, ledger.path, tmp, "cli")
        events: list = []
        native = _ok(call("run_research", json.dumps(_research_args(root, ledger.path, tmp,
                                                                     "native")),
                          lambda kind, data: events.append((kind, data))))
        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(browser_jobs, "_isolate", lambda: False)  # as under Emscripten
            browser = _ok(call("run_research", json.dumps(
                _research_args(root, ledger.path, tmp, "browser"))))
        finally:
            mp.undo()
    finally:
        if old is None:
            os.environ.pop("BIOAGENT_FORMULA_TABLE", None)
        else:
            os.environ["BIOAGENT_FORMULA_TABLE"] = old
    return {"tmp": tmp, "root": root, "ledger": ledger.path, "cli": cli, "native": native,
            "browser": browser, "events": events}


def _manifest(out: Path) -> dict:
    return json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the research run

def test_run_research_writes_the_bytes_bioagent_research_writes(planted):
    tmp, cli = planted["tmp"], planted["cli"]
    assert cli.returncode == 0, cli.stderr[-2000:]
    reference = tmp / "out-cli"
    for tag in ("native", "browser"):
        result = planted[tag]
        assert result["exit"] == 0 and result["failed"] == [] and result["refused"] is None
        out = Path(result["summary"]["output_dir"])
        assert out == (tmp / f"out-{tag}").resolve()
        for name in SAME_BYTES:
            assert (out / name).read_bytes() == (reference / name).read_bytes(), (tag, name)
        assert _manifest(out)["result_digest"] == _manifest(reference)["result_digest"]
        a, b = _manifest(out), _manifest(reference)
        a.pop("execution")
        b.pop("execution")
        assert a == b, "everything but the execution block is the same"


def test_its_summary_is_what_the_command_prints(planted):
    printed = json.loads(planted["cli"].stdout)
    for tag in ("native", "browser"):
        summary = dict(planted[tag]["summary"])
        assert set(summary) == set(printed)
        for key in ("run_id", "audit_head", "output_dir"):
            summary.pop(key)
        expected = {k: v for k, v in printed.items()
                    if k not in ("run_id", "audit_head", "output_dir")}
        assert summary == expected, tag
    assert planted["native"]["summary"]["run_id"] == printed["run_id"]


def test_in_the_browser_the_analysis_runs_in_process_and_says_so(planted):
    def isolation(tag: str) -> dict:
        doc = json.loads((Path(planted[tag]["summary"]["output_dir"]) / "artifact.json")
                         .read_text(encoding="utf-8"))
        return {s: r["isolation"] for s, r in doc["provenance"]["execution"].items()}

    assert isolation("browser") == {"analyse": "in_process", "rebut": "in_process"}
    assert isolation("native") == {"analyse": "process", "rebut": "process"}
    for tag in ("native", "browser"):
        assert planted[tag]["summary"]["governed_execution"] is True
        assert planted[tag]["summary"]["tool_calls"] >= 1


def test_run_research_logs_through_emit(planted):
    kinds = {kind for kind, _ in planted["events"]}
    texts = [data["text"] for _, data in planted["events"]]
    assert kinds == {"log"} and any("released" in t for t in texts)
    assert all(set(data) == {"level", "text"} for _, data in planted["events"])


def test_verify_research_passes_the_run_and_the_runners_checks(planted):
    out = Path(planted["browser"]["summary"]["output_dir"])
    args = {"out_dir": str(out), "snapshot_root": str(planted["root"]),
            "ledger": str(planted["ledger"])}
    result = _ok(call("verify_research", json.dumps(args)))
    assert result["status"] == "PASS" and result["problems"] == []
    names = [c["name"] for c in result["checks"]]
    assert {"manifest_digests", "enrichment_arithmetic", "result_digest", "tables",
            "required_artefacts", "validator:manifest", "validator:artifact"} <= set(names)
    assert result["report"]["status"] == "PASS"
    # without the snapshots nothing is recomputed, and it says so
    alone = _ok(call("verify_research", json.dumps({"out_dir": str(out)})))
    assert alone["status"] == "UNVERIFIED"


def test_verify_research_fails_an_edited_table(planted, tmp_path):
    import shutil
    out = tmp_path / "copy"
    shutil.copytree(Path(planted["native"]["summary"]["output_dir"]), out)
    with (out / "targets.tsv").open("a", encoding="utf-8") as fh:
        fh.write("P99999\tedited\n")
    result = _ok(call("verify_research", json.dumps({
        "out_dir": str(out), "snapshot_root": str(planted["root"]),
        "ledger": str(planted["ledger"])})))
    assert result["status"] == "FAIL"
    assert any(p.startswith("manifest_digests: targets.tsv") for p in result["problems"])
    assert any(p.startswith("artefact 'manifest'") and "targets.tsv" in p
               for p in result["problems"])


def test_the_command_line_gives_the_same_bytes(planted):
    tmp = planted["tmp"]
    args = _research_args(planted["root"], planted["ledger"], tmp, "module")
    res = subprocess.run([sys.executable, "-m", "tcmstudio.browser_jobs", "run_research",
                          "--args", json.dumps(args, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=600, env=_env(), cwd=tmp)
    assert res.returncode == 0, res.stderr[-2000:]
    result = json.loads(res.stdout)["result"]
    assert result["exit"] == 0
    for name in SAME_BYTES:
        assert (tmp / "out-module" / name).read_bytes() == (tmp / "out-cli" / name).read_bytes()
    assert "log: research" in res.stderr


def test_a_refused_question_is_exit_2_with_the_reason(planted, tmp_path):
    args = {**_research_args(planted["root"], planted["ledger"], tmp_path, "r"),
            "question": "不存在汤的靶点"}
    result = _ok(call("run_research", json.dumps(args)))
    assert result["exit"] == 2 and result["summary"] is None
    assert "no known formula" in result["refused"]
    assert not (tmp_path / "out-r" / "run_manifest.json").exists()


# ------------------------------------------------------------------ the snapshot build

@pytest.fixture
def fixture_raw(tmp_path, monkeypatch):
    """The parser tests' raw files (real headers, the marker compounds of 葛根芩连汤), with
    the specs holding the fixtures' own checksums and sizes in place of the reviewed ones,
    as BioScience-Harness/tests/test_snapshot_pipeline.py does; nothing is served."""
    if not (HARNESS_TESTS / "test_snapshot_pipeline.py").is_file():
        pytest.skip("BioScience-Harness tests are not beside this checkout")
    from bioagent.acquisition.sources import ACQUIRABLE
    from bioagent.sources import pipeline

    sys.path.insert(0, str(HARNESS_TESTS))
    try:
        from test_snapshot_pipeline import _raw
    finally:
        sys.path.remove(str(HARNESS_TESTS))
    raw = _raw(tmp_path / "raw")
    specs, first = {}, set()
    for name, a in pipeline._SPECS.items():
        path = raw / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        reviewed = a in ACQUIRABLE
        checksum = ("sha256:" + hashlib.sha256(data).hexdigest()
                    if reviewed and a.source_project not in first else None)
        first.add(a.source_project)
        specs[name] = replace(a, checksum=checksum,
                              expected_bytes=len(data) if reviewed else None)
    monkeypatch.setattr(pipeline, "_SPECS", specs)
    return raw


def test_build_snapshots_is_the_offline_build(fixture_raw, tmp_path):
    from bioagent.sources import pipeline

    events: list = []
    a, b = tmp_path / "a", tmp_path / "b"
    report = _ok(call("build_snapshots", json.dumps({
        "raw_dir": str(fixture_raw), "snapshot_root": str(a),
        "ledger": str(a / "ledger.jsonl"), "report_path": str(tmp_path / "report.json")}),
        lambda kind, data: events.append((kind, data))))
    direct = pipeline.build_research_snapshots(fixture_raw, b, b / "ledger.jsonl",
                                               offline=True, log=lambda m: None)
    assert [s["stage"] for s in report["stages"]] == [
        "discovery", "acquisition", "integrity", "build", "ledger"]
    stages = {s["stage"]: s["status"] for s in report["stages"]}
    assert stages == {s["stage"]: s["status"] for s in direct["stages"]}
    assert stages["discovery"] == stages["acquisition"] == stages["build"] == \
        stages["ledger"] == "PASS"
    # the two files with no reviewed checksum or size: kept and said so, never passed
    assert stages["integrity"] == "UNVERIFIED" and report["status"] == "UNVERIFIED"
    assert report["parameters"]["offline"] is True
    assert {f["route"] for f in report["stages"][1]["files"]} == {"local"}
    ids = {k: v["snapshot_id"] for k, v in report["snapshots"].items()}
    assert ids == {k: v["snapshot_id"] for k, v in direct["snapshots"].items()}
    assert set(ids) == {"tcm_herbs", "npass", "cmaup", "lotus", "string", "reactome"}
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["status"] == \
        report["status"]
    lines = [data["text"] for kind, data in events if kind == "log"]
    assert len(lines) == len(pipeline.required_files()) and all(": PASS" in t for t in lines)
    # and the plan of the same directory: every file here
    plan = _ok(call("plan_snapshots", json.dumps({"raw_dir": str(fixture_raw)})))
    assert plan["missing"] == [] and plan["required"] == [
        a.filename for a in pipeline.required_files()]


def test_the_browser_path_end_to_end_on_the_fixture_files(fixture_raw, tmp_path, monkeypatch):
    """build, research (in process, as the browser does) and verify, all through call()."""
    snaps = tmp_path / "snap"
    _ok(call("build_snapshots", json.dumps({"raw_dir": str(fixture_raw),
                                            "snapshot_root": str(snaps),
                                            "ledger": str(snaps / "ledger.jsonl")})))
    monkeypatch.setattr(browser_jobs, "_isolate", lambda: False)
    result = _ok(call("run_research", json.dumps({
        "question": "葛根芩连汤的实测靶点是否集中在某条通路？", "snapshot_root": str(snaps),
        "ledger": str(snaps / "ledger.jsonl"), "state_dir": str(tmp_path / "state"),
        "out_dir": str(tmp_path / "out"), "permutations": 200})))
    assert result["exit"] in (0, 1) and result["summary"]["governed_execution"] is True
    checked = _ok(call("verify_research", json.dumps({
        "out_dir": str(tmp_path / "out"), "snapshot_root": str(snaps),
        "ledger": str(snaps / "ledger.jsonl")})))
    assert checked["status"] in ("PASS", "UNVERIFIED") and checked["problems"] == []


def test_a_file_that_is_not_the_reviewed_one_fails_the_build(fixture_raw, tmp_path):
    from bioagent.sources import pipeline
    name = next(n for n, a in pipeline._SPECS.items() if a.checksum)
    with (fixture_raw / name).open("a", encoding="utf-8") as fh:
        fh.write("tampered\n")
    report = _ok(call("build_snapshots", json.dumps({
        "raw_dir": str(fixture_raw), "snapshot_root": str(tmp_path / "s"),
        "ledger": str(tmp_path / "s" / "ledger.jsonl")})))
    assert report["status"] == "FAIL" and report["stages"][-1]["stage"] == "integrity"


# ------------------------------------------------------------------ errors are data

@pytest.mark.parametrize("fn, args, needle", [
    ("nope", "{}", "no function 'nope'"),
    ("run_research", "not json", "not JSON"),
    ("run_research", "[1, 2]", "a JSON object"),
    ("run_research", json.dumps({"question": QUESTION, "bogus": 1}), "unknown argument(s) bogus"),
    ("run_research", json.dumps({"question": QUESTION}), "snapshot_root: required"),
    ("run_research", json.dumps({"question": 3}), "question: a string"),
    ("run_research", json.dumps({"question": QUESTION, "permutations": "many"}), "permutations"),
    ("run_research", json.dumps({"question": QUESTION, "background": "all"}), "background"),
    ("build_snapshots", json.dumps({"raw_dir": "r", "snapshot_root": "s", "ledger": "l",
                                    "sources": ["chembl"]}), "no research source"),
    ("plan_snapshots", json.dumps({"raw_dir": "r", "network": "yes"}), "network: true or false"),
    ("verify_research", json.dumps({"out_dir": "o", "ledger": "l"}), "both snapshot_root"),
])
def test_bad_arguments_are_refused_before_anything_runs(fn, args, needle):
    error = _error(call(fn, args))
    assert error["type"] == "bad_arguments" and needle in error["message"], error


def test_failures_are_typed_and_call_never_raises(monkeypatch):
    def missing(args, emit):
        raise ModuleNotFoundError("No module named 'pyarrow'", name="pyarrow")

    def broken(args, emit):
        raise RuntimeError("boom")

    def interrupted(args, emit):
        raise KeyboardInterrupt

    def not_json(args, emit):
        return {"value": float("nan")}

    functions = dict(browser_jobs.FUNCTIONS)
    monkeypatch.setattr(browser_jobs, "FUNCTIONS", {
        **functions, "missing": missing, "broken": broken, "interrupted": interrupted,
        "not_json": not_json})
    assert _error(call("missing"))["type"] == "refused"
    assert "pyarrow" in _error(call("missing"))["message"]
    assert _error(call("broken")) == {"type": "runtime_error", "message": "RuntimeError: boom"}
    assert _error(call("interrupted"))["type"] == "cancelled"
    assert _error(call("not_json"))["type"] == "runtime_error"


def test_the_command_line_exit_codes(tmp_path):
    def run(*argv):
        return subprocess.run([sys.executable, "-m", "tcmstudio.browser_jobs", *argv],
                              capture_output=True, text=True, timeout=120, env=_env(),
                              cwd=tmp_path)
    bad = run("run_research", "--args", "{}")
    assert bad.returncode == 2 and json.loads(bad.stdout)["error"]["type"] == "bad_arguments"
    plan = run("plan_snapshots", "--args", json.dumps({"raw_dir": str(tmp_path)}))
    assert plan.returncode == 0 and len(json.loads(plan.stdout)["result"]["missing"]) == 17
    (tmp_path / "args.json").write_text(json.dumps({"raw_dir": str(tmp_path)}))
    assert run("plan_snapshots", "--args", f"@{tmp_path / 'args.json'}").returncode == 0


def test_importing_the_module_loads_nothing_beyond_the_standard_library(tmp_path):
    code = ("import sys; import tcmstudio.browser_jobs; "
            "print(sorted(m for m in ('bioagent', 'psh', 'pyarrow', 'numpy') "
            "if m in sys.modules))")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=120, env=_env(), cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "[]"


# ------------------------------------------------------------------ the dataset registry

def test_the_registry_lists_the_research_files_as_the_build_reads_them():
    from bioagent.acquisition.sources import ACQUIRABLE
    from bioagent.sources.pipeline import RESEARCH_SOURCES, required_files
    from bioagent.tcmdb import DATASETS

    from tcmstudio.datasets import REDISTRIBUTION_UNKNOWN, licence_of, registry_document

    doc = registry_document()
    assert doc["schema"] == "tcmstudio.datasets/1" and set(doc["groups"]) == {"research"}
    group = doc["groups"]["research"]
    assert group["sources"] == list(RESEARCH_SOURCES) == ["npass", "cmaup", "lotus"]
    assert group["network"] is True and set(group["title"]) == {"zh", "en"}
    files = {f["name"]: f for f in group["files"]}
    specs = required_files(RESEARCH_SOURCES, network=True)
    assert [f["name"] for f in group["files"]] == [a.filename for a in specs]
    assert len(files) == 17
    for a in specs:
        f = files[a.filename]
        assert (f["url"], f["bytes"], f["checksum"], f["license"], f["project"]) == \
            (a.url, a.expected_bytes, a.checksum, a.license, a.source_project)
        assert f["host"] == urllib.parse.urlsplit(a.url).hostname
        assert f["reviewed"] is (a in ACQUIRABLE)
        assert f["relay"] is True and f["redirect_hosts"] == []
    # every reviewed checksum, LOTUS's as Zenodo publishes it (md5); none invented
    assert sum(1 for f in files.values() if f["checksum"]) == 14
    assert files["260413_frozen.csv.gz"]["checksum"] == "md5:cf0cf2afa2ca4d758b68f2e39d466f5d"
    for name in ("9606.protein.aliases.v12.0.txt.gz", "UniProt2Reactome.txt"):
        assert files[name]["reviewed"] is False
        assert files[name]["checksum"] is None and files[name]["bytes"] is None
    assert files["uniprot_human_reviewed_accessions.txt"]["checksum"] is None
    # redistribution as the runner's dataset pages say it: from a licence record that names
    # the release, else unknown
    tcmdb = {d.key: d for d in DATASETS}
    for key, project in (("npass", "NPASS"), ("cmaup", "CMAUP"), ("lotus", "LOTUS")):
        stated = {f["redistribution"] for f in files.values() if f["project"] == project}
        assert stated == {licence_of(tcmdb[key])["redistribution"]}, key
    assert files["260413_frozen.csv.gz"]["redistribution"] == "with attribution (CC BY 4.0)"
    assert files["9606.protein.links.v12.0.txt.gz"]["redistribution"] == \
        "with attribution, indicating changes (CC BY 4.0)"
    for name in ("NPASSv2.0_download_naturalProducts_activities.txt", "UniProt2Reactome.txt",
                 "uniprot_human_reviewed_accessions.txt"):
        assert files[name]["redistribution"] == REDISTRIBUTION_UNKNOWN, name


def test_the_published_registry_is_byte_stable_with_sorted_keys():
    from tcmstudio import webbuild

    data = webbuild.datasets_bytes()
    assert data == webbuild.datasets_bytes()
    text = data.decode("utf-8")
    assert text.endswith("\n") and json.loads(text)["groups"]["research"]["files"]
    first = json.loads(text)["groups"]["research"]["files"][0]
    assert list(first) == sorted(first)
