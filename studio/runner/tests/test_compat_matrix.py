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
    # nothing that only the runner can run is reported as running in the browser
    for r in table:
        if "browser" not in r["exec_now"]:
            assert r["class"] in ("6", "7", "8"), r["id"]


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
