"""``tcmstudio corpus build|fetch|info``: fetching a published snapshot makes the corpus
tools work offline on the runner."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tcmstudio.cli import main
from tcmstudio.corpus.reader import reset_default_corpora
from tcmstudio.dispatch import call
from test_corpus_support import serve_directory, tiny_corpus

pytest.importorskip("openpyxl")


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    return tiny_corpus(tmp_path_factory)


def test_fetch_then_the_tools_work_offline(tiny, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TCMSTUDIO_CORPUS", raising=False)
    home = tmp_path / "home"
    with serve_directory(tiny["site"]) as base:
        assert main(["corpus", "fetch", "--home", str(home), "--url",
                     base + "corpus/latest.json", "--json"]) == 0
        first = json.loads(capsys.readouterr().out)
        assert main(["corpus", "fetch", "--home", str(home), "--url",
                     base + "corpus/latest.json", "--json"]) == 0
        again = json.loads(capsys.readouterr().out)
    assert first["snapshot_id"] == tiny["summary"]["snapshot_id"]
    assert first["objects"] == first["downloaded"] > 0 and first["already_cached"] == 0
    assert again["downloaded"] == 0 and again["already_cached"] == first["objects"]
    cached = home / "corpus"
    assert (cached / "latest.json").is_file() and (cached / tiny["summary"]["manifest"]).is_file()
    # a damaged copy is noticed and fetched again
    victim = sorted((cached / "o").glob("*.gz"))[0]
    victim.write_bytes(b"x")
    with serve_directory(tiny["site"]) as base:
        assert main(["corpus", "fetch", "--home", str(home), "--url",
                     base + "corpus/latest.json", "--json"]) == 0
        repaired = json.loads(capsys.readouterr().out)
    assert repaired["downloaded"] == 1
    # the server is gone and web access is off: the runner reads its copy
    reset_default_corpora()
    import tcmstudio.corpus.reader as R
    monkeypatch.setattr(R, "_site_candidates", lambda: [])
    monkeypatch.setattr(R.P, "LATEST_URL", "http://127.0.0.1:9/corpus/latest.json")
    env = call("corpus_formula", {"name": "人参散", "source": "千金"},
               {"where": "runner", "network": False, "state_root": str(home / "projects")})
    assert env["status"] == "succeeded", env["error"]
    assert env["receipt"]["corpus"]["snapshot_id"] == tiny["summary"]["snapshot_id"]
    assert "cache" in env["receipt"]["corpus"]["source"]


def test_info_and_build_commands(tiny, tmp_path, capsys):
    assert main(["corpus", "info", "--source", str(tiny["site"]), "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["snapshot_id"] == tiny["summary"]["snapshot_id"]
    assert set(info["packs"]) == {"core", "formulas", "lotus", "pathways"}
    assert main(["corpus", "info", "--source", str(tiny["site"])]) == 0
    text = capsys.readouterr().out
    assert tiny["summary"]["snapshot_id"] in text and "LicenseRef-owner-published-unstated" in text
    assert main(["corpus", "info", "--source", str(tmp_path / "nowhere")]) == 1
    out = tmp_path / "built"
    proc = subprocess.run([sys.executable, "-m", "tcmstudio", "corpus", "build", "--out", str(out),
                           "--xlsx", str(tiny["xlsx"]), "--data", str(tiny["data"]), "--no-cache",
                           "--json"], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout)["manifest"] == tiny["summary"]["manifest"]
    assert "corpus tcmcorpus-" in proc.stderr
    assert main(["corpus"]) == 2
