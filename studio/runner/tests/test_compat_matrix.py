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
