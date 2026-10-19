"""The CI workflows run the checks the docs say they run (.github/workflows): the generated reports and the docs' numbers
on every push, the pipeline stage tests where the engines are installed, the live connector run on a schedule and never
on a push, and the real-Pyodide specs failing in CI rather than passing as skips."""
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"


def _load(name):
    doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return doc, doc.get("on", doc.get(True))           # YAML 1.1 reads the bare key `on` as true


def _runs(job):
    return "\n".join(str(s.get("run", "")) for s in job.get("steps", []))


def test_every_push_checks_the_docs_numbers_the_matrix_and_the_connector_coverage():
    ci, on = _load("ci.yml")
    assert on["push"]["branches"] == ["**"]
    run = _runs(ci["jobs"]["validation"])
    for check in ("studio/scripts/doc_facts.py --check", "studio/scripts/compat_matrix.py --check",
                  "studio/scripts/connector_coverage.py --check"):
        assert check in run, check
    studio, _ = _load("studio.yml")
    test = _runs(studio["jobs"]["test"])
    assert "studio/scripts/doc_facts.py --check" in test and "pytest -q studio/runner/tests" in test
    # studio.yml runs only on its paths, which do not list the other workflows: this test runs on every push too
    assert "pytest -q studio/runner/tests/test_ci_wiring.py" in run


@pytest.mark.parametrize("job", ["analysis-tools", "molecular-tools"])
def test_the_stage_and_reference_tests_run_where_the_engines_are_installed(job):
    ci, _ = _load("ci.yml")
    steps = [s for s in ci["jobs"][job]["steps"] if "pytest" in str(s.get("run", ""))]
    assert any("tests/test_runrecord.py" in s["run"] and "tests/test_pipeline_references.py" in s["run"]
               and (s.get("env") or {}).get("BIOAGENT_REQUIRE_TOOLS") == "1" for s in steps), job


def test_the_live_connector_run_is_scheduled_reported_and_never_committed():
    wf, on = _load("connectors-live.yml")
    assert set(on) == {"schedule", "workflow_dispatch"}, "never on a push or a pull request"
    assert on["schedule"] and all(re.fullmatch(r"\S+ \S+ \S+ \S+ \S+", c["cron"]) for c in on["schedule"])
    assert wf["permissions"] == {"contents": "read"}
    text = (WORKFLOWS / "connectors-live.yml").read_text(encoding="utf-8")
    steps = [s for job in wf["jobs"].values() for s in job["steps"]]
    assert any(str(s.get("uses", "")).startswith("actions/upload-artifact@") for s in steps)
    run = "\n".join(str(s.get("run", "")) for s in steps)
    assert re.search(r'verify_connectors\.py --out "\$RUNNER_TEMP/', run), "the run writes outside the checkout"
    assert "connector_coverage.py --live-record" in run
    assert not re.search(r"\bgit\s+(commit|push|add)\b", text)


def test_no_push_triggered_job_calls_the_real_upstreams():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf, on = _load(path.name)
        if path.name in ("live-data.yml",):              # tier 3/4: on its own paths and weekly, by design
            continue
        if not isinstance(on, dict) or not ({"push", "pull_request"} & set(on)):
            continue
        for name, job in wf["jobs"].items():
            if "verify_connectors.py" in _runs(job) or "-m integration" in _runs(job):
                assert "workflow_dispatch" in str(job.get("if", "")), f"{path.name}:{name} reaches the network on a push"


def test_the_real_pyodide_specs_fail_in_ci_instead_of_skipping():
    lib = (REPO / "studio" / "e2e" / "lib" / "servers.mjs").read_text(encoding="utf-8")
    assert "export function pyodideSkipReason" in lib and "export function requirePyodide" in lib
    for name in ("browser.spec.mjs", "browser-access.spec.mjs", "mobile.spec.mjs"):
        spec = (REPO / "studio" / "e2e" / name).read_text(encoding="utf-8")
        assert "requirePyodide(test, reachable, boot.pyodide.index_url," in spec, name
        assert "test.skip(!reachable" not in spec, name
