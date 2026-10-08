"""The site build and the Python bundle the browser runtime unpacks (CONTRACTS §5).

The bundle is checked the way the worker uses it: unpacked into a directory that is the
only place the packages can come from, then imported by a separate interpreter that runs a
governed skill. Pyodide itself is exercised in Chromium by studio/web/test/runtime/run.mjs.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tcmstudio import webbuild

STUDIO = Path(__file__).resolve().parents[2]
SAFETY_HASH = "e525e3abd5011488dd01b5a2720b8256e7c75766d49383287fe214271b173312"


@pytest.fixture(scope="module")
def bundle() -> webbuild.Bundle:
    return webbuild.build_bundle()


@pytest.fixture(scope="module")
def members(bundle) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(bundle.data), mode="r:gz") as tar:
        return {m.name: m for m in tar.getmembers()}


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("site") / "_site"
    return out, webbuild.build(out, dev=False)


def test_bundle_is_reproducible_and_named_by_its_hash(bundle):
    again = webbuild.build_bundle()
    assert again.data == bundle.data
    assert bundle.sha256 == hashlib.sha256(bundle.data).hexdigest()
    assert bundle.name == f"tcms-py.{bundle.sha256[:12]}.tar.gz"
    # gzip header: no file name, mtime 0
    assert bundle.data[:2] == b"\x1f\x8b"
    assert bundle.data[3] & 0x08 == 0
    assert bundle.data[4:8] == b"\0\0\0\0"


def test_bundle_entries_are_normalised(members):
    names = list(members)
    assert names == sorted(names)
    for m in members.values():
        assert m.isfile()
        assert (m.mtime, m.uid, m.gid, m.uname, m.gname, m.mode) == \
            (1_577_836_800, 0, 0, "", "", 0o644)
        assert not m.name.startswith(("/", "..")) and "/../" not in m.name


def test_bundle_holds_the_three_packages_as_installed(members, bundle):
    names = set(members)
    for pkg in ("bioagent", "psh", "tcmstudio"):
        assert f"{pkg}/__init__.py" in names
    assert "tcmstudio/dispatch.py" in names and "tcmstudio/catalog.py" in names
    # the reviewed skills, registry and lockfile, where an installed bioagent reads them
    assert "bioagent/_bundled/registry/skills.lock.yaml" in names
    assert "bioagent/_bundled/skills/tcm/assess-tcm-safety/skill.yaml" in names
    assert any(n.startswith("bioagent/_bundled/skills/candidates/") for n in names)
    assert "bioagent/data/clinic_pack.json" in names                       # package data
    assert "bioagent/data/unified_capability_catalogue.csv" not in names    # unused in a browser
    assert not any("__pycache__" in n or n.endswith((".pyc", ".pyo")) for n in names)
    assert not any(n.startswith("tcmstudio/web/") for n in names)
    assert bundle.versions == webbuild._versions()


def test_dist_info_reports_honest_versions(members, bundle):
    with tarfile.open(fileobj=io.BytesIO(bundle.data), mode="r:gz") as tar:
        for dist in ("bioagent", "psh", "tcmstudio"):
            version = bundle.versions[dist]
            meta = tar.extractfile(f"{dist}-{version}.dist-info/METADATA").read().decode()
            assert f"Name: {dist}\n" in meta and f"Version: {version}\n" in meta
            top = tar.extractfile(f"{dist}-{version}.dist-info/top_level.txt").read().decode()
            assert top.strip() == dist


def test_boot_document_matches_the_contract(bundle):
    catalog, doc = webbuild.catalog_bytes()
    boot = webbuild.boot_document(bundle, catalog)
    assert boot["schema"] == "tcmstudio.boot/1"
    assert boot["pyodide"] == {"version": "314.0.7",
                               "index_url": "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/",
                               "packages": ["pyyaml", "packaging", "sqlite3"]}
    assert boot["bundle"]["path"] == f"runtime/{bundle.name}"
    assert boot["bundle"]["sha256"] == bundle.sha256
    assert boot["bundle"]["bytes"] == len(bundle.data)
    assert boot["bundle"]["python_path"] == [boot["bundle"]["extract_dir"]]
    assert boot["catalog"] == "runtime/catalog.json"
    assert boot["catalog_sha256"] == hashlib.sha256(catalog).hexdigest()
    assert boot["state_root"] == "/persist"
    assert set(boot["versions"]) == {"tcmstudio", "bioagent", "psh"}
    assert doc["where"] == "browser" and "problems" not in doc
    assert json.loads(catalog) == doc


def test_index_url_override(monkeypatch, bundle):
    catalog, _ = webbuild.catalog_bytes()
    boot = webbuild.boot_document(bundle, catalog, index_url="/pyodide")
    assert boot["pyodide"]["index_url"] == "/pyodide/"
    monkeypatch.setenv(webbuild.ENV_INDEX_URL, "https://example.org/py/")
    assert webbuild.boot_document(bundle, catalog)["pyodide"]["index_url"] == "https://example.org/py/"


def test_browser_catalog_matches_the_cli(tmp_path):
    out = tmp_path / "cat.json"
    subprocess.run([sys.executable, "-m", "tcmstudio", "catalog", "--where", "browser",
                    "--no-probe", "--out", str(out)], check=True, capture_output=True)
    catalog, _ = webbuild.catalog_bytes()
    assert json.loads(catalog) == json.loads(out.read_text(encoding="utf-8"))


def _yaml_path() -> str:
    spec = importlib.util.find_spec("yaml")
    assert spec and spec.origin
    return str(Path(spec.origin).parent.parent)


def test_unpacked_bundle_runs_a_governed_skill_like_the_browser(tmp_path, bundle):
    """Only the bundle (and PyYAML, which the worker loads from Pyodide) on the path."""
    site = tmp_path / "site"
    with tarfile.open(fileobj=io.BytesIO(bundle.data), mode="r:gz") as tar:
        tar.extractall(site, filter="data")
    yaml_only = tmp_path / "yaml"
    yaml_only.mkdir()
    src = Path(_yaml_path())
    for name in ("yaml", "_yaml"):
        if (src / name).exists():
            os.symlink(src / name, yaml_only / name)
    script = f"""
import json, sys
sys.path[:0] = [{str(site)!r}, {str(yaml_only)!r}]
import bioagent.config as c
from importlib.metadata import version
from tcmstudio.dispatch import call
e = call("tcm_safety_report", {{"subject": "甘草", "co_administered": ["甘遂"]}},
         {{"where": "browser", "state_root": {str(tmp_path / 'persist')!r}, "project_id": "p"}})
print(json.dumps({{"source_tree": c.SOURCE_TREE, "skills": str(c.skills_dir()),
                  "versions": [version("bioagent"), version("psh"), version("tcmstudio")],
                  "files": [m.__file__ for m in (sys.modules["bioagent"], sys.modules["psh"],
                                                 sys.modules["tcmstudio"])],
                  "status": e["status"], "released": e["governance"]["released"],
                  "hash": e["receipt"]["content_hash"], "where": e["receipt"]["where"]}}))
"""
    res = subprocess.run([sys.executable, "-S", "-I", "-c", script], capture_output=True,
                         text=True, cwd=tmp_path, timeout=300)
    assert res.returncode == 0, res.stderr[-2000:]
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["source_tree"] is False
    assert out["skills"] == str(site / "bioagent" / "_bundled" / "skills")
    assert all(f.startswith(str(site)) for f in out["files"])
    assert out["versions"] == [bundle.versions[d] for d in ("bioagent", "psh", "tcmstudio")]
    assert (out["status"], out["released"], out["hash"], out["where"]) == \
        ("succeeded", True, SAFETY_HASH, "browser")


def test_build_writes_the_site(site, bundle):
    out, summary = site
    boot = json.loads((out / "runtime" / "boot.json").read_text(encoding="utf-8"))
    data = (out / boot["bundle"]["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == boot["bundle"]["sha256"] == bundle.sha256
    assert (out / "runtime" / "catalog.json").is_file()
    assert (out / "index.html").is_file()
    assert (out / "js" / "runtime" / "browser.js").is_file()
    assert (out / "js" / "runtime" / "pyodide.worker.js").is_file()
    assert not (out / "test").exists() and not (out / "dev").exists()
    assert summary["bundle"]["sha256"] == bundle.sha256
    headers = STUDIO / "edge" / "_headers"
    assert (out / "_headers").is_file() == headers.is_file()
    if headers.is_file():
        assert (out / "_headers").read_bytes() == headers.read_bytes()
    assert not list(out.parent.glob(f".{out.name}.*")), "no staging directory left behind"


def test_dev_build_includes_the_test_pages(tmp_path):
    out = tmp_path / "dev_site"
    webbuild.build(out, dev=True)
    assert (out / "test" / "runtime" / "index.html").is_file()
    assert (out / "test" / "runtime" / "check.mjs").is_file()


def test_rebuild_replaces_an_earlier_build_but_never_a_foreign_directory(tmp_path):
    out = tmp_path / "out"
    webbuild.build(out)
    (out / "stale.txt").write_text("old", encoding="utf-8")
    webbuild.build(out)
    assert not (out / "stale.txt").exists()

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(webbuild.BuildError, match="does not look like an earlier build"):
        webbuild.build(foreign)
    assert (foreign / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_build_refuses_an_output_inside_the_web_app(tmp_path):
    web = webbuild.default_web_dir()
    assert web is not None
    with pytest.raises(webbuild.BuildError, match="inside the web app"):
        webbuild.build(web / "_site")
    with pytest.raises(webbuild.BuildError, match="no web app"):
        webbuild.build(tmp_path / "x", web_dir=tmp_path / "nothing")


def test_runtime_file_serves_the_same_files_from_memory(bundle):
    webbuild.clear_cache()
    boot_bytes, media = webbuild.runtime_file("boot.json")
    assert media.startswith("application/json")
    boot = json.loads(boot_bytes)
    name = boot["bundle"]["path"].split("/", 1)[1]
    data, media = webbuild.runtime_file(name)
    assert media == "application/gzip"
    assert data == bundle.data
    assert webbuild.runtime_file("runtime/boot.json")[0] == boot_bytes
    assert webbuild.runtime_file("/runtime/catalog.json")[1].startswith("application/json")
    assert webbuild.runtime_file("tcms-py.000000000000.tar.gz") is None
    assert webbuild.runtime_file("../boot.json") is None
    assert webbuild.runtime_file("") is None
    # cached per process: the same objects come back
    assert webbuild.runtime_file("boot.json")[0] is boot_bytes


def test_cli_and_wrapper_build(tmp_path):
    out = tmp_path / "cli"
    res = subprocess.run([sys.executable, "-m", "tcmstudio", "webbuild", "--out", str(out),
                          "--json"], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[-2000:]
    summary = json.loads(res.stdout)
    assert summary["bundle"]["name"].startswith("tcms-py.")
    assert (out / "runtime" / "boot.json").is_file()

    out2 = tmp_path / "wrapper"
    res = subprocess.run([sys.executable, str(STUDIO / "scripts" / "build_web.py"),
                          "--out", str(out2)], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[-2000:]
    boot = (out / "runtime" / "boot.json").read_bytes()
    assert (out2 / "runtime" / "boot.json").read_bytes() == boot

    bad = subprocess.run([sys.executable, "-m", "tcmstudio", "webbuild",
                          "--out", str(tmp_path / "x"), "--web", str(tmp_path / "missing")],
                         capture_output=True, text=True, timeout=120)
    assert bad.returncode == 2 and "no web app" in bad.stderr


def test_gzip_stream_is_a_plain_tar(bundle):
    raw = gzip.decompress(bundle.data)
    assert raw[257:262] == b"ustar"
