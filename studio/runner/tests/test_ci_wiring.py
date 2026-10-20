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


def _job_with(wf, needle):
    """The steps of the job whose runs include ``needle``, up to and including the step that does."""
    for job in wf["jobs"].values():
        steps = job.get("steps", [])
        for i, step in enumerate(steps):
            if needle in str(step.get("run", "")):
                return steps[: i + 1]
    raise AssertionError(f"no job runs {needle}")


def _installs_pins(steps):
    return any("rdkit==2026.9.1" in str(s.get("run", "")) and "scikit-learn==1.8.0" in str(s.get("run", ""))
               for s in steps)


def test_studio_runs_the_browser_chemistry_kernels_and_admet_jobs_with_the_native_side_at_the_browsers_pins():
    studio, on = _load("studio.yml")
    for command in ("studio/web/test/runtime/chem.browser.mjs", "studio/web/test/runtime/gpu-science.mjs --software"):
        _job_with(studio, command)
    # RDKit 2026.9.1 and scikit-learn 1.8.0 installed in the job, before the step that needs them: the fixture's
    # regeneration, the native side of --mode admet, and the runner's ADMET tests (which skip without them)
    for command in ("studio/scripts/make_chem_fixtures.py --check", "science.mjs --mode admet",
                    "pytest -q studio/runner/tests"):
        assert _installs_pins(_job_with(studio, command)), command
    # the native side of --mode admet imports admet_world: a change to it runs the workflow
    for event in ("push", "pull_request"):
        assert "BioScience-Harness/tests/admet_world.py" in on[event]["paths"], event


def test_live_data_builds_the_admet_models_from_the_real_archive_natively_and_in_a_browser():
    wf, on = _load("live-data.yml")
    assert "live" in wf["jobs"] and "admet" in wf["jobs"], "a job of its own: the existing job's steps stay as they are"
    job = wf["jobs"]["admet"]
    run = _runs(job)
    assert "fetch_archive" in run and "TDC_ADMET_SHA256" in run, "the archive downloaded natively, held to its pin"
    assert "curl -sIL" in run and "redirect hosts" in run, "its redirect chain printed for redirect_hosts"
    assert re.search(r"science\.mjs --mode admet --archive \"\$RUNNER_TEMP/admet/tdc_admet_group\.zip\"", run)
    assert _installs_pins(job["steps"])
    assert any(str(s.get("uses", "")).startswith("actions/upload-artifact@") for s in job["steps"])
    verdict = job["steps"][-1]
    assert verdict.get("if") == "always()" and "steps.browser_admet.outcome" in verdict["run"]
    # the browser ADMET code and the harness that runs it trigger the workflow
    for path in ("BioScience-Harness/src/bioagent/admet/**", "studio/web/test/runtime/admet*"):
        assert path in on["push"]["paths"], path
    # the existing job still ends with its own verdict over its own steps
    live = wf["jobs"]["live"]
    assert "steps.browser_gqd.outcome" in live["steps"][-1]["run"] and "admet" not in _runs(live)


def test_live_data_admet_leaves_the_native_side_its_time_and_reviews_only_hosts_it_was_sent_to(tmp_path):
    import subprocess
    wf, _ = _load("live-data.yml")
    steps = {s.get("id"): s for s in wf["jobs"]["admet"]["steps"] if s.get("id")}
    # the page's limit leaves the step time for the native side, which runs after the page has answered (the 22 models
    # at one thread, then the pipeline and every test split's predictions): else the report is lost with the step
    browser = steps["browser_admet"]
    page = int(re.search(r"--timeout-min (\d+)", browser["run"]).group(1))
    assert browser["timeout-minutes"] - page >= 30, (page, browser["timeout-minutes"])
    # redirect_hosts: the hosts of absolute Location headers; a relative one stays on the host already asked
    line = next(x.strip() for x in steps["redirects"]["run"].splitlines() if x.strip().startswith('hosts="$('))
    chain = tmp_path / "chain.txt"
    chain.write_text("HTTP/2 303\nlocation: /api/access/datafile/4426004?gbrecs=true\nHTTP/2 302\n"
                     "Location: https://dvn-cloud.s3.amazonaws.com/10.7910/DVN/x?X-Amz-Expires=3600\nHTTP/1.1 200 OK\n",
                     encoding="utf-8")
    hosts = subprocess.run(["bash", "-c", f'out="$1"; {line}; printf "%s" "$hosts"', "_", str(chain)],
                           capture_output=True, text=True, check=True).stdout
    assert hosts.split() == ["dvn-cloud.s3.amazonaws.com"], hosts
