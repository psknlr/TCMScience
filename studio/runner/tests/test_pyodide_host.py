"""Self-hosted Pyodide (docs/V2.md §15): pinned core files, the lockfile closure of what the site loads, the cache,
and the refusals (a wrong hash, a file over the limit). Offline: a fake downloader serves a fake Pyodide."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tcmstudio import pyodide_host as H
from tcmstudio import webbuild


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _world(extra_packages: dict | None = None):
    """A fake CDN: core files, a lockfile and wheels. Returns (files by name, pins for the core files)."""
    wheels = {
        "numpy-2.4.6-cp314-cp314-pyemscripten_2026_0_wasm32.whl": b"numpy wheel",
        "scipy-1.18.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl": b"scipy wheel",
        "pyyaml-6.0.3-cp314-cp314-pyemscripten_2026_0_wasm32.whl": b"pyyaml wheel",
        "packaging-26.1-py3-none-any.whl": b"packaging wheel",
        "pandas-3.0.2-cp314-cp314-pyemscripten_2026_0_wasm32.whl": b"pandas wheel",
        "pytz-2026.1-py2.py3-none-any.whl": b"pytz wheel",
        "python_dateutil-2.9.0.post0-py2.py3-none-any.whl": b"dateutil wheel",
        "six-1.17.0-py2.py3-none-any.whl": b"six wheel",
    }
    pkg = lambda file, deps=(): {"file_name": file, "sha256": _sha(wheels[file]), "depends": list(deps),  # noqa: E731
                                 "package_type": "package", "install_dir": "site"}
    packages = {
        "numpy": pkg("numpy-2.4.6-cp314-cp314-pyemscripten_2026_0_wasm32.whl"),
        "scipy": pkg("scipy-1.18.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl", ["numpy"]),
        "pyyaml": pkg("pyyaml-6.0.3-cp314-cp314-pyemscripten_2026_0_wasm32.whl"),
        "packaging": pkg("packaging-26.1-py3-none-any.whl"),
        "pandas": pkg("pandas-3.0.2-cp314-cp314-pyemscripten_2026_0_wasm32.whl", ["numpy", "python-dateutil", "pytz"]),
        "pytz": pkg("pytz-2026.1-py2.py3-none-any.whl"),
        "python-dateutil": pkg("python_dateutil-2.9.0.post0-py2.py3-none-any.whl", ["six"]),
        "six": pkg("six-1.17.0-py2.py3-none-any.whl"),
        **(extra_packages or {}),
    }
    lock = json.dumps({"info": {"python": "3.14.2"}, "packages": packages}).encode()
    core = {"pyodide.mjs": b"export const loadPyodide = 1;", "pyodide.asm.mjs": b"asm", "pyodide.asm.wasm": b"\0asm\1\0\0\0",
            "python_stdlib.zip": b"PK stdlib", "pyodide-lock.json": lock}
    files = {**core, **wheels}
    return files, {name: _sha(data) for name, data in core.items()}


class FakeCDN:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.requests: list[str] = []

    def __call__(self, url: str, max_bytes: int) -> bytes:
        self.requests.append(url)
        name = url.rsplit("/", 1)[-1]
        if name not in self.files:
            raise OSError(f"HTTP 404 {url}")
        return self.files[name]


CATALOG = {"entries": [{"id": "native.a", "pyodide_packages": ["numpy"]},
                       {"id": "study.b", "pyodide_packages": ["scipy", "numpy"]},
                       {"id": "native.c"}]}


def test_the_pins_are_the_314_0_7_core_files():
    assert H.PYODIDE_VERSION == "314.0.7"
    assert set(H.CORE_FILES) == {"pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip",
                                 "pyodide-lock.json"}
    assert all(len(v) == 64 and int(v, 16) >= 0 for v in H.CORE_FILES.values())
    assert H.CORE_FILES["pyodide.asm.wasm"] == "cc36e3cab04fdfc9a63ff13eb52eae2b911bf46c025cc7b281f394bd3de1d5e6"
    assert H.INDEX_URL == "pyodide/314.0.7/"
    assert H.CDN_URL == "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/"
    assert H.MAX_FILE_BYTES == 25 * 1024 * 1024


def test_wanted_packages_and_their_closure():
    assert H.wanted_packages(CATALOG) == sorted({"pyyaml", "packaging", "sqlite3", "numpy", "scipy"})
    assert H.wanted_packages(None) == sorted(webbuild.PYODIDE_PACKAGES)
    files, _ = _world()
    lock = json.loads(files["pyodide-lock.json"])
    assert H.closure(lock, ["scipy"]) == (["numpy", "scipy"], [])
    assert H.closure(lock, ["pandas"]) == (["numpy", "pandas", "python-dateutil", "pytz", "six"], [])
    assert H.closure(lock, ["Python_Dateutil", "sqlite3"]) == (["python-dateutil", "six"], ["sqlite3"]), \
        "names are compared as Pyodide normalises them; a name it does not list is reported"


def test_self_host_copies_the_core_and_the_closure_and_returns_the_index(tmp_path):
    files, pins = _world()
    cdn = FakeCDN(files)
    logs: list[str] = []
    out = H.self_host(tmp_path / "site", catalog=CATALOG, log=logs.append, download=cdn,
                      cache=tmp_path / "cache", pins=pins)
    assert out["index_url"] == "pyodide/314.0.7/"
    assert out["packages"] == ["numpy", "packaging", "pyyaml", "scipy"]
    assert out["missing"] == ["sqlite3"]
    assert out["files"] == 9 and out["downloaded"] == 9
    hosted = sorted(p.name for p in (tmp_path / "site" / "pyodide" / "314.0.7").iterdir())
    assert hosted == sorted([*pins, "numpy-2.4.6-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
                             "scipy-1.18.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
                             "pyyaml-6.0.3-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
                             "packaging-26.1-py3-none-any.whl"])
    assert "pandas-3.0.2-cp314-cp314-pyemscripten_2026_0_wasm32.whl" not in hosted, "only what the site loads"
    assert out["bytes"] == sum(len(files[n]) for n in hosted)
    assert all(f["sha256"] == _sha(files[f["path"].rsplit("/", 1)[-1]]) for f in out["listing"])
    assert all(u.startswith(H.CDN_URL) for u in cdn.requests)
    lock_copy = (tmp_path / "site" / "pyodide" / "314.0.7" / "pyodide-lock.json").read_bytes()
    assert lock_copy == files["pyodide-lock.json"], "the lockfile is published as pinned"
    assert any("sqlite3" in line for line in logs)


def test_the_cache_is_used_and_checked(tmp_path):
    files, pins = _world()
    cdn = FakeCDN(files)
    kw = dict(catalog=CATALOG, download=cdn, cache=tmp_path / "cache", pins=pins)
    H.self_host(tmp_path / "a", **kw)
    first = len(cdn.requests)
    out = H.self_host(tmp_path / "b", **kw)
    assert len(cdn.requests) == first and out["downloaded"] == 0, "everything from the cache"
    (tmp_path / "cache" / "pyodide.asm.wasm").write_bytes(b"damaged")
    out = H.self_host(tmp_path / "c", **kw)
    assert out["downloaded"] == 1 and cdn.requests[-1].endswith("/pyodide.asm.wasm"), "a damaged copy is fetched again"
    assert (tmp_path / "c" / "pyodide" / "314.0.7" / "pyodide.asm.wasm").read_bytes() == files["pyodide.asm.wasm"]


def test_cache_dir_follows_tcmstudio_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("TCMSTUDIO_CACHE", str(tmp_path))
    assert H.cache_dir() == tmp_path / "pyodide" / "314.0.7"
    monkeypatch.delenv("TCMSTUDIO_CACHE")
    assert H.cache_dir() == Path("~/.cache/tcmstudio/pyodide/314.0.7").expanduser()


def test_a_file_that_does_not_match_its_pin_is_refused(tmp_path):
    files, pins = _world()
    files = {**files, "pyodide.asm.mjs": b"tampered"}
    with pytest.raises(H.PyodideHostError, match="pyodide.asm.mjs: SHA-256 .* does not match the pinned"):
        H.self_host(tmp_path / "site", catalog=CATALOG, download=FakeCDN(files), cache=tmp_path / "cache", pins=pins)
    assert not (tmp_path / "cache" / "pyodide.asm.mjs").exists(), "never kept"


def test_a_wheel_is_checked_against_the_lockfile(tmp_path):
    files, pins = _world()
    files = {**files, "numpy-2.4.6-cp314-cp314-pyemscripten_2026_0_wasm32.whl": b"not numpy"}
    with pytest.raises(H.PyodideHostError, match="numpy-2.4.6.*does not match"):
        H.self_host(tmp_path / "site", catalog=CATALOG, download=FakeCDN(files), cache=tmp_path / "cache", pins=pins)


def test_a_file_over_the_limit_is_refused(tmp_path):
    files, pins = _world()
    with pytest.raises(H.PyodideHostError, match="over the 12-byte limit"):
        H.self_host(tmp_path / "site", catalog=CATALOG, download=FakeCDN(files), cache=tmp_path / "cache",
                    pins=pins, max_bytes=12)


def test_a_download_failure_is_a_build_error_naming_the_url(tmp_path):
    files, pins = _world()
    del files["scipy-1.18.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl"]
    with pytest.raises(webbuild.BuildError, match=r"could not download https://cdn\.jsdelivr\.net/.*scipy"):
        H.self_host(tmp_path / "site", catalog=CATALOG, download=FakeCDN(files), cache=tmp_path / "cache", pins=pins)


def test_a_lockfile_that_points_elsewhere_is_refused(tmp_path):
    files, pins = _world({"evil": {"file_name": "https://example.com/evil.whl", "sha256": "0" * 64, "depends": []}})
    pins["pyodide-lock.json"] = _sha(files["pyodide-lock.json"])
    with pytest.raises(H.PyodideHostError, match="not a file beside it"):
        H.self_host(tmp_path / "site", catalog={"entries": [{"pyodide_packages": ["evil"]}]}, download=FakeCDN(files),
                    cache=tmp_path / "cache", pins=pins)


def test_webbuild_self_host_points_boot_json_at_the_site(tmp_path, monkeypatch):
    files, pins = _world()
    monkeypatch.setattr(H, "CORE_FILES", pins)
    monkeypatch.setattr(H, "_http_download", FakeCDN(files))
    monkeypatch.setenv("TCMSTUDIO_CACHE", str(tmp_path / "cache"))
    summary = webbuild.build(tmp_path / "site", corpus=False, self_host_pyodide=True)
    boot = json.loads((tmp_path / "site" / "runtime" / "boot.json").read_text(encoding="utf-8"))
    assert boot["pyodide"]["index_url"] == "pyodide/314.0.7/"
    assert boot["pyodide"]["version"] == "314.0.7"
    assert summary["extras"]["pyodide"]["index_url"] == "pyodide/314.0.7/"
    assert (tmp_path / "site" / "pyodide" / "314.0.7" / "pyodide.mjs").read_bytes() == files["pyodide.mjs"]
    wanted = set(summary["extras"]["pyodide"]["packages"])
    assert {"pyyaml", "packaging"} <= wanted
