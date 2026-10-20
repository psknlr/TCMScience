"""The CI workflows run the checks the docs say they run (.github/workflows): the generated reports and the docs' numbers
on every push, the pipeline stage tests where the engines are installed, the live connector run on a schedule and never
on a push, the real-Pyodide specs failing in CI rather than passing as skips, and the browser docking's WebAssembly
Vina rebuilt from its pins and held to native Vina 1.2.7."""
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


DOCK_PINS = ("vina==1.2.7", "meeko==0.8.0", "gemmi==0.7.5")


def _installs_dock_pins(steps):
    return any(all(pin in str(s.get("run", "")) for pin in DOCK_PINS) for s in steps)


def test_studio_runs_the_browser_docking_with_the_native_side_at_the_engines_pins():
    studio, on = _load("studio.yml")
    # Vina 1.2.7 (the WebAssembly build's release; prepared docking refuses any other), Meeko and gemmi at the
    # fixtures' pins, installed in the job before the steps that need them: the fixtures' regeneration and the native
    # side of --mode dock
    harness = "node studio/web/test/runtime/science.mjs --mode dock"
    for command in ("python studio/scripts/make_dock_fixtures.py --check", harness):
        assert _installs_dock_pins(_job_with(studio, command)[:-1]), command
    step = _job_with(studio, harness)[-1]
    assert '--json "$RUNNER_TEMP/science-dock.json"' in step["run"], "its report is kept with the others"
    uploads = [s for s in studio["jobs"]["build"]["steps"]
               if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert any(s.get("if") == "always()" and s["with"]["path"].endswith("science-*.json") for s in uploads)
    # the native side copies the prepared fixtures, and make_dock_fixtures.py reads 3PTB's pocket: a change runs it
    for event in ("push", "pull_request"):
        assert "BioScience-Harness/tests/fixtures/docking/**" in on[event]["paths"], event


def test_studio_rebuilds_the_webassembly_vina_from_its_pins_with_the_inputs_cached_on_them(tmp_path):
    import os
    import runpy
    import subprocess
    import sys
    studio, _ = _load("studio.yml")
    job = studio["jobs"]["vina-wasm"]
    steps = job["steps"]
    check = next(s for s in steps if "studio/scripts/build_vina_wasm.py --check" in str(s.get("run", "")))
    cache = next(s for s in steps if str(s.get("uses", "")).startswith("actions/cache@"))
    pins = next(s for s in steps if s.get("id") == "pins")
    assert steps.index(pins) < steps.index(cache) < steps.index(check)
    assert cache["with"]["key"] == "vina-wasm-inputs-${{ steps.pins.outputs.key }}"
    assert "restore-keys" not in cache["with"], "only the pinned inputs, never another pin's"
    assert re.search(r'--cache "\$RUNNER_TEMP/vina-wasm-inputs"', check["run"])
    assert cache["with"]["path"] == "${{ runner.temp }}/vina-wasm-inputs"
    assert any(str(s.get("uses", "")).startswith("actions/setup-node@") for s in steps), "Emscripten runs node"
    # the site is built and deployed only from a build that reproduces
    assert "vina-wasm" in studio["jobs"]["build"]["needs"]
    # the key is the three pins' SHA-256, read from the script itself (run here as the step runs it)
    out = tmp_path / "out"
    path = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    subprocess.run(["bash", "-c", pins["run"]], cwd=REPO, env={"PATH": path, "GITHUB_OUTPUT": str(out)}, check=True)
    script = runpy.run_path(str(REPO / "studio" / "scripts" / "build_vina_wasm.py"))
    want = "-".join(script[k]["sha256"][:16] for k in ("EMSCRIPTEN", "VINA", "BOOST"))
    assert out.read_text(encoding="utf-8").strip() == f"key={want}"


def test_molecular_tools_runs_the_prepared_docking_tests_with_real_vina_127():
    ci, _ = _load("ci.yml")
    steps = ci["jobs"]["molecular-tools"]["steps"]
    at = next(i for i, s in enumerate(steps) if "tests/test_docking_prepared.py" in str(s.get("run", "")))
    assert (steps[at].get("env") or {}).get("BIOAGENT_REQUIRE_TOOLS") == "1", "a skip of the real Vina test fails here"
    assert _installs_dock_pins(steps[:at]), "vina 1.2.7, which the prepared mode requires, installed before"


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
