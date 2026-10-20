"""studio/docs/compat (the browser compatibility matrix) is generated from the catalog and kept current."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "compat_matrix.py"


def test_the_committed_matrix_matches_the_catalog():
    res = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr


def test_every_entry_has_a_class_and_the_kernels_are_where_the_code_is():
    sys.path.insert(0, str(SCRIPT.parent))
    import compat_matrix
    table = compat_matrix.rows()
    assert all(r["class"] in compat_matrix.CLASS_LABELS for r in table)
    by_id = {r["id"]: r for r in table}
    for tool in compat_matrix.KERNEL:
        assert by_id[tool]["class"] == "2"
    for tool in compat_matrix.GPU:
        assert by_id[tool]["class"] == "3"
    # nothing that only the runner can run is reported as running in the browser, and nothing the browser runs
    # (by its exec, the routing) is reported as the runner's: the two counts are one count
    for r in table:
        if "browser" not in r["exec_now"]:
            assert r["class"] in ("6", "7", "8") and r["browser_scope"] == "runner only", r["id"]
        else:
            assert r["class"] in ("1", "1g", "1j", "2", "3", "5"), r["id"]
            assert r["browser_scope"] == "all arguments" or r["browser_scope"].startswith("some arguments: "), r["id"]


def test_classes_backends_and_validation_come_from_the_catalog():
    sys.path.insert(0, str(SCRIPT.parent))
    import compat_matrix
    doc = compat_matrix.catalog()
    entries = {e["id"]: e for e in doc["entries"]}
    by_id = {r["id"]: r for r in compat_matrix.rows()}
    kernels = {i for i, e in entries.items() if (e.get("execution") or {}).get("browser_compute")}
    gpu = {i for i in kernels if "webgpu" in entries[i]["execution"]["browser_compute"]["backends"]}
    assert {i for i, r in by_id.items() if r["class"] == "3"} == gpu and len(gpu) == 3
    assert {i for i, r in by_id.items() if r["class"] == "2"} == kernels - gpu and len(kernels - gpu) == 4
    # the hand-written notes cover exactly the tools the catalog says have a kernel
    assert set(compat_matrix.KERNEL) | set(compat_matrix.GPU) == kernels
    for i, r in by_id.items():
        levels = entries[i]["verification"]["levels"]
        if not levels:
            assert r["validation"] == "UNVERIFIED: no committed verification record", i
        else:
            for name, lv in levels.items():
                assert f"{name} {lv['status']}" in r["validation"], i
        assert "byte-identical to native CPython (runtime check)" not in r["validation"]
    assert by_id["connector.uniprot.entry"]["validation"].startswith("live_upstream PASS (")
    sources, operations = compat_matrix.connector_counts(doc)
    text = compat_matrix.summary(compat_matrix.rows())
    assert f"Public database connectors ({sources} sources, {operations} operations)" in text
    assert sources == len({e["connector"]["key"] for e in entries.values() if e["kind"] == "connector"})


def test_the_admet_jobs_are_browser_jobs_and_the_governed_skill_stays_on_the_runner():
    sys.path.insert(0, str(SCRIPT.parent))
    import compat_matrix
    from tcmstudio.browser_jobs import ADMET_MOLECULES_MAX
    by_id = {r["id"]: r for r in compat_matrix.rows()}
    for eid in ("job.admet.models", "job.pipeline.admet"):
        assert by_id[eid]["class"] == "1j" and "scikit-learn" in by_id[eid]["local_backends"], eid
        assert "RDKit.js" in by_id[eid]["notes"], eid
    assert f"more than {ADMET_MOLECULES_MAX:,} molecules" in by_id["job.pipeline.admet"]["notes"]
    assert by_id["skill.predict-admet"]["class"] == "7"
    text = compat_matrix.summary(compat_matrix.rows())
    assert "| ADMET from structure: the model build and the predictions | job.admet.models, job.pipeline.admet | 1j |" in text


def test_docking_prepared_inputs_is_a_browser_job_for_some_arguments():
    """job.pipeline.dock runs in the browser on prepared inputs, its Vina compiled to WebAssembly in workers of
    its own (not a Pyodide job worker); preparing inputs and the governed skill stay on the runner. The matrix
    counts the entries the browser runs for every argument apart from those it runs for some."""
    sys.path.insert(0, str(SCRIPT.parent))
    import compat_matrix
    from tcmstudio.browser_jobs import DOCK_LIGANDS_MAX
    table = compat_matrix.rows()
    by_id = {r["id"]: r for r in table}
    dock = by_id["job.pipeline.dock"]
    assert dock["class"] == "1j" and dock["exec_now"] == "browser+runner"
    assert dock["local_backends"] == ("Pyodide job workers (numpy), and AutoDock Vina 1.2.7 compiled to WebAssembly "
                                      "in dedicated workers + OPFS → the runner's job service")
    assert dock["browser_scope"] == "some arguments: receptor, ligands, site_ligand, allow_remote"
    assert f"more than {DOCK_LIGANDS_MAX} ligands" in dock["notes"] and "PDBQT" in dock["notes"]
    assert "job.pipeline.dock" not in compat_matrix.RUNNER_REASONS
    assert by_id["skill.dock-ligands"]["class"] == "7" and by_id["skill.dock-ligands"]["browser_scope"] == "runner only"
    assert by_id["job.pipeline.admet"]["browser_scope"] == "some arguments: molecules"
    assert by_id["job.research.run"]["browser_scope"] == "all arguments"
    assert by_id["native.bmi"]["browser_scope"] == "all arguments"
    for eid in ("job.admet.models", "job.pipeline.admet", "job.pipeline.rnaseq"):
        assert by_id[eid]["local_backends"].startswith("Pyodide job workers (") and "Vina" not in by_id[eid]["local_backends"]
    browser = [r for r in table if "browser" in r["exec_now"]]
    every = [r for r in browser if r["browser_scope"] == "all arguments"]
    some = [r["id"] for r in browser if r["browser_scope"].startswith("some")]
    assert some == ["job.pipeline.admet", "job.pipeline.dock", "job.pipeline.rnaseq", "job.pipeline.scrna"]
    text = compat_matrix.summary(table)
    assert (f"of the {len(browser)} entries the browser runs, it runs {len(every)} for every argument and "
            f"{len(some)} for some arguments only ({', '.join(some)})") in text
    assert f"{len(table) - len(browser)} entries run on the runner only" in text
    assert "| Docking prepared inputs (PDBQT): AutoDock Vina 1.2.7 compiled to WebAssembly | " in text
