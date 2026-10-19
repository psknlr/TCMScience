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
import shutil
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


def test_a_cancelled_run_is_data_and_resumes_where_it_stopped(planted, tmp_path, monkeypatch):
    """The browser cancels a job by interrupting its Python (KeyboardInterrupt), here in the
    second analysis call, under the broker. call() says so, and the job resumed on the same
    state reuses the analysis it finished and writes the bytes ``bioagent research`` wrote."""
    from psh.kernel.egress import ExecutionBroker

    invoke, calls = ExecutionBroker._invoke, []

    def interrupted(self, *args, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return invoke(self, *args, **kw)

    monkeypatch.setattr(browser_jobs, "_isolate", lambda: False)  # as under Emscripten
    args = json.dumps(_research_args(planted["root"], planted["ledger"], tmp_path, "c"))
    with monkeypatch.context() as m:
        m.setattr(ExecutionBroker, "_invoke", interrupted)
        assert _error(call("run_research", args))["type"] == "cancelled"
    result = _ok(call("run_research", args))
    assert result["exit"] == 0 and "analyse" in result["summary"]["resumed_stages"]
    for name in SAME_BYTES:
        assert (tmp_path / "out-c" / name).read_bytes() == \
            (planted["tmp"] / "out-cli" / name).read_bytes(), name


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
    # the runner's research.run bounds (kinds._research_prepare), not just "a positive number"
    ("run_research", json.dumps({"question": QUESTION, "permutations": 5_000_000}),
     "permutations: between 100 and 100000"),
    ("run_research", json.dumps({"question": QUESTION, "permutations": 10}),
     "permutations: between 100 and 100000"),
    ("run_research", json.dumps({"question": QUESTION, "activity": ["NPASS v2"]}),
     "activity: 'NPASS v2' is not in the expected form"),
    ("run_research", json.dumps({"question": QUESTION, "disease": "diarrhoea"}),
     "disease: 'diarrhoea' is not in the expected form"),
    # a wrongly typed formula is an argument error, not the analysis refusing the question
    ("run_research", json.dumps({"question": QUESTION, "formula": 7}), "formula: a string"),
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

    def exits(args, emit):
        sys.exit(4)

    functions = dict(browser_jobs.FUNCTIONS)
    monkeypatch.setattr(browser_jobs, "FUNCTIONS", {
        **functions, "missing": missing, "broken": broken, "interrupted": interrupted,
        "not_json": not_json, "exits": exits})
    assert _error(call("missing"))["type"] == "refused"
    assert "pyarrow" in _error(call("missing"))["message"]
    assert _error(call("broken")) == {"type": "runtime_error", "message": "RuntimeError: boom"}
    assert _error(call("interrupted"))["type"] == "cancelled"
    assert _error(call("not_json"))["type"] == "runtime_error"
    assert _error(call("exits")) == {"type": "runtime_error",
                                     "message": "exits tried to exit (code 4)"}


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


# ------------------------------------------------------------------ the omics pipelines

PIPELINE_SKIP = ("run.json", "stages.json")


def _harness(name: str):
    """A helper module of BioScience-Harness/tests (the simulated worlds), or a skip."""
    import importlib
    if not (HARNESS_TESTS / f"{name}.py").is_file():
        pytest.skip("BioScience-Harness tests are not beside this checkout")
    sys.path.insert(0, str(HARNESS_TESTS))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(HARNESS_TESTS))


def _outputs(out: Path) -> dict[str, bytes]:
    """Every file a run wrote but its record and stage file (they hold times and paths)."""
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*"))
            if p.is_file() and p.name not in PIPELINE_SKIP}


def _bioagent(tmp: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "bioagent.cli", *argv], capture_output=True,
                          text=True, timeout=600, env=_env(), cwd=tmp)


def _same_summary(result: dict, printed: dict) -> None:
    a, b = dict(result["summary"]), dict(printed)
    assert Path(a.pop("out_dir")).name and Path(b.pop("out_dir")).name
    assert a == b


@pytest.fixture(scope="module")
def counts_runs(tmp_path_factory):
    """The RNA-seq count-matrix reference, run by ``bioagent rnaseq --counts`` (a child
    process) and by ``browser_jobs.run_rnaseq_counts``."""
    tmp = tmp_path_factory.mktemp("counts")
    world = _harness("omics_world").make_counts_world(tmp / "world", seed=1)
    cli = _bioagent(tmp, "rnaseq", "--counts", str(world["counts"]), "--samples",
                    str(world["samples"]), "--contrast", "condition,treated,control",
                    "--out", str(tmp / "cli"), "--json")
    events: list = []
    result = _ok(call("run_rnaseq_counts", json.dumps({
        "counts": str(world["counts"]), "samples": str(world["samples"]),
        "contrast": "condition,treated,control", "out_dir": str(tmp / "fn")}),
        lambda kind, data: events.append((kind, data))))
    return {"tmp": tmp, "world": world, "cli": cli, "result": result, "events": events}


@pytest.fixture(scope="module")
def sc_tables(tmp_path_factory):
    """A small single-cell world (sc_world, a fifth of the reference's cell types' cells) as
    the browser reads one: a gzipped count table per sample (genes in rows) and a sheet of
    them with the samples' condition and batch."""
    import csv
    import gzip

    from bioagent.omics.sc import io as sc_io

    tmp = tmp_path_factory.mktemp("sc-tables")
    world = _harness("sc_world").make_sc_world(tmp / "world", seed=0, per_type=60, path_cells=80)
    rows = list(csv.DictReader(open(world.sheet, encoding="utf-8")))
    sheet = ["sample,path,condition,batch"]
    for r in rows:
        m = sc_io.read_counts(world.root / r["path"])
        counts = m.counts.T.toarray().astype(int)
        with gzip.GzipFile(tmp / f"{r['sample']}.tsv.gz", "wb", mtime=0) as fh:
            fh.write(("gene\t" + "\t".join(m.cells) + "\n").encode())
            for name, row in zip(m.gene_names, counts):
                fh.write((name + "\t" + "\t".join(map(str, row)) + "\n").encode())
        sheet.append(f"{r['sample']},{r['sample']}.tsv.gz,{r['condition']},{r['batch']}")
    (tmp / "sheet.csv").write_text("\n".join(sheet) + "\n", encoding="utf-8")
    return tmp


@pytest.fixture(scope="module")
def sc_runs(sc_tables):
    cli = _bioagent(sc_tables, "scrna", str(sc_tables / "sheet.csv"), "--n-top-genes", "600",
                    "--out", str(sc_tables / "cli"), "--json")
    events: list = []
    result = _ok(call("run_scrna", json.dumps({
        "source": str(sc_tables / "sheet.csv"), "n_top_genes": 600,
        "out_dir": str(sc_tables / "fn")}), lambda kind, data: events.append((kind, data))))
    return {"tmp": sc_tables, "cli": cli, "result": result, "events": events}


def test_run_rnaseq_counts_writes_the_bytes_the_command_writes(counts_runs):
    tmp, cli, result = counts_runs["tmp"], counts_runs["cli"], counts_runs["result"]
    assert cli.returncode == 0, cli.stderr[-2000:]
    assert result["exit"] == 0 and result["failed"] == [] and result["refused"] is None
    assert result["need"] is None
    assert _outputs(tmp / "fn") == _outputs(tmp / "cli")
    assert len(_outputs(tmp / "fn")) == 13              # 4 tables, 6 figures, 2 reports, limitations
    _same_summary(result, json.loads(cli.stdout))
    assert result["summary"]["input_kind"] == "counts"


def test_run_scrna_writes_the_bytes_the_command_writes(sc_runs):
    tmp, cli, result = sc_runs["tmp"], sc_runs["cli"], sc_runs["result"]
    assert cli.returncode == 0, cli.stderr[-2000:]
    assert result["exit"] == 0 and result["failed"] == [] and result["refused"] is None
    produced = _outputs(tmp / "fn")
    assert produced == _outputs(tmp / "cli")
    assert {"cells.tsv", "clusters.tsv", "markers.tsv", "report.html", "limitations.md"} <= set(produced)
    _same_summary(result, json.loads(cli.stdout))
    assert result["summary"]["integration"] == "harmony"


def test_the_pipelines_say_what_they_do_as_they_do_it(counts_runs, sc_runs):
    from bioagent.omics import rnaseq_counts, scrna
    for runs, stages in ((counts_runs, rnaseq_counts.STAGES), (sc_runs, scrna.STAGES)):
        events = runs["events"]
        logs = [data["text"] for kind, data in events if kind == "log"]
        assert len(logs) >= 2 and logs[-1].endswith(": complete")
        progress = [data for kind, data in events if kind == "progress"]
        fractions = [p["fraction"] for p in progress]
        assert fractions == sorted(fractions) and fractions[-1] == 1.0
        ran = {s["name"] for s in json.loads((runs["tmp"] / "fn" / "run.json").read_text())["stages"]
               if s["status"] == "succeeded"}
        assert ran <= {p["stage"] for p in progress} <= set(stages) | {None}


@pytest.mark.parametrize("kind, sub", [("pipeline.rnaseq", "counts_runs"), ("pipeline.scrna", "sc_runs")])
def test_verify_pipeline_makes_the_runners_checks(kind, sub, request, tmp_path):
    from types import SimpleNamespace

    from tcmstudio.jobs import JobService
    from tcmstudio.kinds import KINDS

    runs = request.getfixturevalue(sub)
    job = tmp_path / "job"
    shutil.copytree(runs["tmp"] / "fn", job / "out")
    events: list = []
    checked = _ok(call("verify_pipeline", json.dumps({"kind": kind, "out_dir": str(job / "out")}),
                       lambda k, data: events.append((k, data))))
    assert checked["status"] == "PASS" and checked["problems"] == []
    assert [data["text"] for k, data in events if k == "log"] == [
        f"verify {kind}: PASS, {len(checked['checks'])} checks"]
    # the checks the runner's collection makes, by its names, after the pipeline's own
    assert [c["name"] for c in checked["checks"]] == [
        "verify_run", "required_artefacts", *(f"validator:{n}" for n in KINDS[kind].validators)]
    report = checked["report"]
    assert report["complete"] is True and report["verify_run"] == {"ok": True, "problems": []}
    assert report["limitations"] == (job / "out" / "limitations.md").read_text().splitlines()
    assert report["pipeline"] == ("bioagent.omics.rnaseq_counts" if kind == "pipeline.rnaseq"
                                  else "bioagent.omics.scrna")
    # a required output gone: the runner's own collection and this check say the same
    required = next(rel for name, rel, need in KINDS[kind].artefacts if need and name != "run")
    (job / "out" / required).unlink()
    runner = JobService._check_artefacts(None, SimpleNamespace(dir=job), KINDS[kind])
    assert runner and any("is missing or empty" in p for p in runner)
    checked = _ok(call("verify_pipeline", json.dumps({"kind": kind, "out_dir": str(job / "out")})))
    assert checked["status"] == "FAIL"
    status = {c["name"]: c["status"] for c in checked["checks"]}
    assert status["verify_run"] == status["required_artefacts"] == status["validator:run"] == "FAIL"
    assert all(p in checked["problems"] for p in runner)
    assert f"output {required} is missing" in checked["problems"]


def test_verify_pipeline_without_a_record_fails_and_says_why(tmp_path):
    checked = _ok(call("verify_pipeline", json.dumps({"kind": "pipeline.scrna",
                                                      "out_dir": str(tmp_path)})))
    assert checked["status"] == "FAIL" and checked["report"]["complete"] is None
    assert checked["problems"][0].startswith("run.json cannot be read")
    assert "required artefact 'run' (run.json) is missing or empty" in checked["problems"]
    # a record the pipeline's check cannot read is a failed check, as the runner's validator says
    (tmp_path / "run.json").write_text(json.dumps({"pipeline": "bioagent.omics.scrna"}))
    checked = _ok(call("verify_pipeline", json.dumps({"kind": "pipeline.scrna",
                                                      "out_dir": str(tmp_path)})))
    assert checked["status"] == "FAIL"
    assert checked["problems"][0] == "the run could not be verified: KeyError: 'inputs'"


@pytest.fixture
def refusable(tmp_path):
    """Sources the browser's single-cell job does not read: a 10x HDF5, an .h5ad, a
    directory, and a sheet naming an HDF5 file."""
    (tmp_path / "pbmc.h5").write_bytes(b"\x89HDF\r\n\x1a\n")
    (tmp_path / "atlas.H5AD").write_bytes(b"\x89HDF\r\n\x1a\n")
    (tmp_path / "filtered_feature_bc_matrix").mkdir()
    (tmp_path / "sheet.csv").write_text("sample,path\nctrl1,pbmc.h5\n")
    (tmp_path / "counts.tsv").write_text("gene_id\ta\tb\ng1\t1\t2\n")
    return tmp_path


@pytest.mark.parametrize("fn, args, needle, need", [
    ("run_rnaseq_counts", {"de_backend": "pydeseq2"},
     "PyDESeq2 is not in this browser's Python; the built-in DESeq2 implementation is", "runner"),
    ("run_scrna", {"analysis_backend": "scanpy"}, "Scanpy, anndata, leidenalg and umap-learn",
     "runner"),
    ("run_scrna", {"integration": "scvi"}, "scVI (scvi-tools and torch) is not in this browser's",
     "runner"),
    ("run_scrna", {"source": "pbmc.h5"}, "pbmc.h5: a 10x HDF5 file is not yet supported in the "
                                         "browser", "runner"),
    ("run_scrna", {"source": "atlas.H5AD"}, "atlas.H5AD: an .h5ad file is read with anndata",
     "runner"),
    ("run_scrna", {"source": "sheet.csv"}, "pbmc.h5: a 10x HDF5 file is not yet supported in the "
                                           "browser", "runner"),
    ("run_scrna", {"source": "filtered_feature_bc_matrix"}, "is a directory (a Cell Ranger output)",
     "runner"),
])
def test_what_the_browser_does_not_run_is_refused_with_the_reason(refusable, fn, args, needle,
                                                                  need):
    base = ({"counts": "counts.tsv", "samples": "samples.tsv"} if fn == "run_rnaseq_counts"
            else {"source": "x.tsv"})
    full = {k: str(refusable / v) if k in ("counts", "samples", "source") else v
            for k, v in {**base, **args}.items()}
    events: list = []
    result = _ok(call(fn, json.dumps({**full, "out_dir": str(refusable / "out")}),
                      lambda kind, data: events.append((kind, data))))
    assert result["exit"] == 2 and result["summary"] is None and result["failed"] == []
    assert needle in result["refused"], result["refused"]
    assert result["need"] == need
    assert not (refusable / "out").exists(), "nothing is written for a refused call"
    assert any(kind == "log" and data["text"].startswith("refused: ") for kind, data in events)


#: The runner's refusal of a FASTQ sample sheet given with counts (kinds._rnaseq_counts_prepare).
FASTQ_SHEET_WITH_COUNTS = ("samples: with counts this is the sample table (sample and the design "
                           "columns); a FASTQ sample sheet runs without counts")


def _corrupt_gzip(text: str) -> bytes:
    """``text`` gzipped, its deflate stream corrupted (bytes 20 to 60 flipped)."""
    import gzip
    raw = gzip.compress(text.encode("utf-8"), mtime=0)
    return raw[:20] + bytes(b ^ 0xFF for b in raw[20:60]) + raw[60:]


@pytest.fixture
def unusable(tmp_path):
    """Inputs nothing can analyse, wherever it runs: a FASTQ sample sheet given as the sample
    table (its header spaced, too), gzipped tables whose deflate stream is corrupt (zlib.error,
    not a truncated file), a count table that is not UTF-8, and a sheet row with no path."""
    counts = "gene_id\tgene_name\t" + "\t".join(f"s{i}" for i in range(6)) + "\n" + "".join(
        f"g{g}\tG{g}\t" + "\t".join(str((g * 7 + i * 3) % 50 + 1) for i in range(6)) + "\n"
        for g in range(200))
    samples = "sample\tcondition\n" + "".join(f"s{i}\t{'ab'[i % 2]}\n" for i in range(6))
    table = "gene\t" + "\t".join(f"c{i}" for i in range(60)) + "\n" + "".join(
        f"G{g}\t" + "\t".join(str((g + i) % 4) for i in range(60)) + "\n" for g in range(300))
    (tmp_path / "counts.tsv").write_text(counts)
    (tmp_path / "samples.tsv").write_text(samples)
    for name, text in (("counts.tsv.gz", counts), ("samples.tsv.gz", samples),
                       ("table.tsv.gz", table)):
        (tmp_path / name).write_bytes(_corrupt_gzip(text))
    (tmp_path / "gz_sheet.csv").write_text("sample,path\nA,table.tsv.gz\n")
    (tmp_path / "fastq.csv").write_text("sample,fastq_1,condition\nctrl1,a.fq.gz,control\n")
    (tmp_path / "spaced.csv").write_text("sample, fastq_1, fastq_2, condition\n"
                                         "ctrl1, a_1.fq.gz, a_2.fq.gz, control\n")
    (tmp_path / "latin.csv").write_bytes(
        table.replace("\t", ",").replace("\nG1,", "\nG\xe9ne1,").encode("latin-1"))
    (tmp_path / "empty_path.csv").write_text("sample,path,condition\nA,,control\n")
    return tmp_path


@pytest.mark.parametrize("fn, args, needle", [
    ("run_rnaseq_counts", {"samples": "fastq.csv"}, FASTQ_SHEET_WITH_COUNTS),
    ("run_rnaseq_counts", {"samples": "spaced.csv"}, FASTQ_SHEET_WITH_COUNTS),
    ("run_rnaseq_counts", {"counts": "counts.tsv.gz"},
     "{dir}/counts.tsv.gz cannot be read to its end (error: Error -3 while decompressing data"),
    ("run_rnaseq_counts", {"samples": "samples.tsv.gz"},
     "{dir}/samples.tsv.gz cannot be read to its end (error: Error -3 while decompressing data"),
    ("run_scrna", {"source": "table.tsv.gz"},
     "{dir}/table.tsv.gz cannot be read to its end (error: Error -3 while decompressing data"),
    ("run_scrna", {"source": "gz_sheet.csv"},
     "{dir}/table.tsv.gz cannot be read to its end (error: Error -3 while decompressing data"),
    ("run_scrna", {"source": "latin.csv"}, "'utf-8' codec can't decode byte 0xe9"),
    ("run_scrna", {"source": "empty_path.csv"}, "line 2 of {dir}/empty_path.csv names no path"),
])
def test_inputs_nothing_can_analyse_are_refused_as_the_runner_refuses_them(
        unusable, capsys, fn, args, needle):
    """Refused with need null (no runner or memory would run them), in the runner's words: a
    FASTQ sheet with counts as the runner's job kind refuses it, the rest as the command
    prints it; never a runtime error, and never 'the runner would run it'."""
    from bioagent import cli
    base = ({"counts": "counts.tsv", "samples": "samples.tsv"} if fn == "run_rnaseq_counts"
            else {})
    full = {k: str(unusable / v) for k, v in {**base, **args}.items()}
    result = _ok(call(fn, json.dumps({**full, "out_dir": str(unusable / "out")})))
    assert (result["exit"], result["need"], result["summary"]) == (2, None, None), result
    assert result["refused"].startswith(needle.replace("{dir}", str(unusable))), result["refused"]
    if needle == FASTQ_SHEET_WITH_COUNTS:
        from tcmstudio.kinds import RNASEQ_COUNTS_FASTQ_SHEET
        assert result["refused"] == RNASEQ_COUNTS_FASTQ_SHEET
        return
    # the command run natively says the same
    argv = (["rnaseq", "--counts", full["counts"], "--samples", full["samples"]]
            if fn == "run_rnaseq_counts" else ["scrna", full["source"]])
    assert cli.main([*argv, "--out", str(unusable / "native")]) == 1
    assert f"refused: {result['refused']}\n" in capsys.readouterr().err


def test_a_table_is_counted_before_it_is_read_in_full(tmp_path, monkeypatch):
    """Whether the source is a sheet is decided from its first line, so a single count table
    is counted (here past a lowered limit) before anything reads it in full."""
    from bioagent.omics import scrna  # noqa: F401 - imported before read_text is watched

    (tmp_path / "t.csv").write_text("gene,c1,c2,c3\nCD3E,1,2,3\n")
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        assert self.name != "t.csv", "the table was read in full before its cells were counted"
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(browser_jobs, "SC_CELLS_MAX", 2)
    result = _ok(call("run_scrna", json.dumps({"source": str(tmp_path / "t.csv"),
                                               "out_dir": str(tmp_path / "out")})))
    assert (result["exit"], result["need"]) == (2, "memory"), result
    assert result["refused"].startswith("3 cells exceed the 2 a browser worker can hold")


def test_a_spaced_header_is_counted_as_the_reader_reads_it(tmp_path, monkeypatch):
    """A CSV header written with ', ' holds the samples the count reader reads (it strips each
    cell): at the samples limit, whatever it is measured to be, the table runs."""
    monkeypatch.setattr(browser_jobs, "RNASEQ_SAMPLES_MAX", 200)
    samples = [f"s{i}" for i in range(200)]
    (tmp_path / "counts.csv").write_text("gene_id, gene_name, " + ", ".join(samples) + "\n" + "".join(
        f"g{g}, G{g}, " + ", ".join(str((g + i) % 9 + 1) for i in range(200)) + "\n"
        for g in range(30)))
    (tmp_path / "samples.csv").write_text("sample,condition\n" + "".join(
        f"{s},{'ab'[i % 2]}\n" for i, s in enumerate(samples)))
    events: list = []
    result = _ok(call("run_rnaseq_counts", json.dumps({
        "counts": str(tmp_path / "counts.csv"), "samples": str(tmp_path / "samples.csv"),
        "out_dir": str(tmp_path / "out")}), lambda kind, data: events.append((kind, data))))
    assert (result["exit"], result["need"]) == (0, None), result
    assert any("(30 genes x 200 samples)" in data["text"] for kind, data in events if kind == "log")


@pytest.mark.parametrize("fn, args, needle", [
    ("run_rnaseq_counts", {"samples": "s", "out_dir": "o"}, "counts: required"),
    ("run_rnaseq_counts", {"counts": "c", "samples": "s", "out_dir": "o", "transcripts": "t"},
     "unknown argument(s) transcripts"),
    ("run_rnaseq_counts", {"counts": "c", "samples": "s", "out_dir": "o", "alpha": 0.9},
     "alpha: between 0.0001 and 0.5"),
    ("run_rnaseq_counts", {"counts": "c", "samples": "s", "out_dir": "o", "alpha": "low"},
     "alpha: a number is needed"),
    ("run_rnaseq_counts", {"counts": "c", "samples": "s", "out_dir": "o", "de_backend": "edger"},
     "de_backend: one of builtin, pydeseq2"),
    ("run_rnaseq_counts", {"counts": "c", "samples": "s", "out_dir": "o", "contrast": "condition"},
     "contrast: 'condition' is not in the expected form"),
    ("run_rnaseq_counts", {"counts": "c", "samples": "s", "out_dir": "o", "design": "-x"},
     "design: a value cannot start with '-'"),
    ("run_scrna", {"source": "s", "out_dir": "o", "resolution": 9}, "resolution: between 0.05 and 5"),
    ("run_scrna", {"source": "s", "out_dir": "o", "seed": -1}, "seed: between 0 and 2147483647"),
    ("run_scrna", {"source": "s", "out_dir": "o", "n_top_genes": 10},
     "n_top_genes: between 50 and 10000"),
    ("run_scrna", {"source": "s", "out_dir": "o", "doublets": "maybe"},
     "doublets: one of remove, flag, off"),
    ("run_scrna", {"source": "s", "out_dir": "o", "contrast": "condition,treated"},
     "contrast: factor,numerator,denominator"),
    ("run_scrna", {"source": "s", "out_dir": "o", "de_backend": "builtin"},
     "unknown argument(s) de_backend"),
    ("verify_pipeline", {"kind": "pipeline.fold", "out_dir": "o"},
     "kind: one of pipeline.rnaseq, pipeline.scrna"),
])
def test_pipeline_arguments_are_held_to_the_runners_checks(fn, args, needle):
    error = _error(call(fn, json.dumps(args)))
    assert error["type"] == "bad_arguments" and needle in error["message"], error


def test_more_than_a_browser_worker_holds_is_refused_before_the_analysis(tmp_path, monkeypatch):
    """wasm32 caps a worker near 4 GB: more cells, genes or samples than the measured limits
    are refused for memory, counted from the tables (gzipped or not) before any is read in
    full, and nothing is written."""
    import gzip

    from tcmstudio import browser_jobs

    def refused(fn, args):
        events: list = []
        result = _ok(call(fn, json.dumps({**args, "out_dir": str(tmp_path / "out")}),
                          lambda kind, data: events.append((kind, data))))
        assert result["exit"] == 2 and result["need"] == "memory", result
        assert not (tmp_path / "out").exists()
        assert any(kind == "log" and data["text"].startswith("refused: ") for kind, data in events)
        return result["refused"]

    # single cell: 6,001 cells over two tables of a sheet, one gzipped (genes in rows)
    def table(cells: int, prefix: str) -> str:
        return "gene\t" + "\t".join(f"{prefix}{i}" for i in range(cells)) + "\nCD3E\t" + \
            "\t".join("1" for _ in range(cells)) + "\n"
    (tmp_path / "a.tsv").write_text(table(3000, "a"))
    with gzip.open(tmp_path / "b.csv.gz", "wt") as fh:
        fh.write(table(3001, "b").replace("\t", ","))
    (tmp_path / "sheet.csv").write_text("sample,path\nA,a.tsv\nB,b.csv.gz\n")
    assert refused("run_scrna", {"source": str(tmp_path / "sheet.csv")}) == (
        "6,001 cells exceed the 6,000 a browser worker can hold (wasm32, 4 GB): run it on the "
        "runner")
    # RNA-seq: 201 samples, or (the limit lowered here) more genes than it allows
    samples = [f"s{i}" for i in range(201)]
    counts = tmp_path / "counts.tsv"
    counts.write_text("gene_id\tgene_name\t" + "\t".join(samples) + "\n" + "".join(
        f"g{g}\tG{g}\t" + "\t".join("5" for _ in samples) + "\n" for g in range(3)))
    (tmp_path / "samples.tsv").write_text("sample\tcondition\n" + "".join(
        f"{s}\t{'a' if i % 2 else 'b'}\n" for i, s in enumerate(samples)))
    rna = {"counts": str(counts), "samples": str(tmp_path / "samples.tsv")}
    assert refused("run_rnaseq_counts", rna) == (
        "3 genes x 201 samples exceed the 100,000 genes x 200 samples a browser worker can hold "
        "(wasm32, 4 GB): run it on the runner")
    monkeypatch.setattr(browser_jobs, "RNASEQ_SAMPLES_MAX", 201)
    monkeypatch.setattr(browser_jobs, "RNASEQ_GENES_MAX", 2)
    assert refused("run_rnaseq_counts", rna).startswith("3 genes x 201 samples exceed the 2 genes")
    # at the limit it runs (here: refused by the pipeline for what the table is, not for memory)
    monkeypatch.setattr(browser_jobs, "RNASEQ_GENES_MAX", 3)
    result = _ok(call("run_rnaseq_counts", json.dumps({**rna, "out_dir": str(tmp_path / "o")})))
    assert result["need"] != "memory"


def test_a_run_that_runs_out_of_memory_is_refused_for_memory(tmp_path, monkeypatch):
    """A MemoryError in a pipeline, or in any function, comes back as a refusal that says
    what would run it, never as a runtime error."""
    from bioagent.omics import rnaseq_counts, scrna

    from tcmstudio import browser_jobs

    def out_of_memory(*args, **kwargs):
        raise MemoryError
    monkeypatch.setattr(scrna, "run_scrna", out_of_memory)
    monkeypatch.setattr(rnaseq_counts, "run_rnaseq_counts", out_of_memory)
    (tmp_path / "t.tsv").write_text("gene\tc1\tc2\nCD3E\t1\t2\n")
    (tmp_path / "counts.tsv").write_text("gene_id\ta\tb\ng1\t1\t2\n")
    result = _ok(call("run_scrna", json.dumps({"source": str(tmp_path / "t.tsv"),
                                               "out_dir": str(tmp_path / "o")})))
    assert (result["exit"], result["need"]) == (2, "memory")
    assert result["refused"] == ("the run ran out of memory (MemoryError) on 2 cells: more than "
                                 "a browser worker can hold (wasm32, 4 GB): run it on the runner")
    result = _ok(call("run_rnaseq_counts", json.dumps({
        "counts": str(tmp_path / "counts.tsv"), "samples": str(tmp_path / "s.tsv"),
        "out_dir": str(tmp_path / "o")})))
    assert (result["exit"], result["need"]) == (2, "memory")
    assert result["refused"].startswith("the run ran out of memory (MemoryError) on 1 genes x 2 "
                                        "samples")
    # any other function: the failure is a refusal, with what would run it beside its message
    monkeypatch.setitem(browser_jobs.FUNCTIONS, "self_test", lambda args, emit: out_of_memory())
    doc = json.loads(call("self_test", "{}"))
    assert doc["ok"] is False and doc["error"] == {
        "type": "refused", "need": "memory",
        "message": "self_test ran out of memory (MemoryError): more than this runtime holds (a "
                   "browser worker, wasm32, at most 4 GB): run it on the runner"}


# ------------------------------------------------------------------ the dataset registry

def test_the_registry_lists_the_research_files_as_the_build_reads_them():
    from bioagent.acquisition.sources import ACQUIRABLE
    from bioagent.sources.pipeline import RESEARCH_SOURCES, required_files
    from bioagent.tcmdb import DATASETS

    from bioagent.acquisition.downloader import PACER

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
        # the relay paces each host as bioagent's acquisition does
        assert f["rate_rps"] == PACER.interval(f["host"]) ** -1
    assert files["260413_frozen.csv.gz"]["rate_rps"] == 0.1      # zenodo.org's Crawl-delay
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


def test_a_count_table_the_csv_reader_cannot_parse_is_refused_not_a_runtime_error(tmp_path):
    # an unclosed quote: the single-cell reader's csv module raises csv.Error
    (tmp_path / "bad.csv").write_text('gene,c1,c2\n"g1,1,2\n')
    result = _ok(call("run_scrna", json.dumps({"source": str(tmp_path / "bad.csv"),
                                                "out_dir": str(tmp_path / "out")})))
    assert result["exit"] == 2 and result["need"] is None, result
    assert result["refused"]
