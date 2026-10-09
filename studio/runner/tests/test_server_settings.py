"""Settings (runner.json), the home's environment, the command line and a real process."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time

import pytest

from tcmstudio import settings as st
from tcmstudio.server import add_arguments, run


def test_validate_merges_and_refuses():
    base = st.default_settings()
    out = st.validate({"device": " CUDA:1 ", "threads": 6.0, "network": True}, base)
    assert out["device"] == "cuda:1" and out["threads"] == 6
    assert out["network"] == {"enabled": True, "profile": "biomedical-research"}
    out = st.validate({"network": {"profile": "offline-analysis"}}, out)
    assert out["network"] == {"enabled": True, "profile": "offline-analysis"}
    assert st.validate({"whatever": 1}, base) == base
    for bad in ({"device": "gpu"}, {"device": 1}, {"threads": True}, {"threads": 1.5},
                {"max_jobs": 0}, {"max_jobs": 99}, {"purpose": "x"}, {"allow_remote": 1},
                {"network": "on"}, {"network": {"enabled": "yes"}}):
        with pytest.raises(st.SettingsError):
            st.validate(bad, base)
    with pytest.raises(st.SettingsError):
        st.validate(["x"], base)


def test_runner_json_keeps_token_and_valid_fields(tmp_path):
    state = st.RunnerState(tmp_path)
    token = state.token
    assert len(token) >= 30
    state.update({"threads": 3, "purpose": "commercial"})
    doc = json.loads((tmp_path / "runner.json").read_text())
    assert doc["schema"] == "tcmstudio.runner/1" and doc["token"] == token
    assert doc["settings"]["threads"] == 3 and doc["settings"]["purpose"] == "commercial"
    assert (tmp_path / "runner.json").stat().st_mode & 0o777 == 0o600
    # a hand-edited file: valid fields kept, the rest back to defaults
    doc["settings"]["threads"] = "many"
    doc["settings"]["max_jobs"] = 4
    (tmp_path / "runner.json").write_text(json.dumps(doc))
    again = st.RunnerState(tmp_path)
    assert again.token == token
    assert again.settings["max_jobs"] == 4
    assert again.settings["threads"] == st.default_settings()["threads"]
    assert again.settings["purpose"] == "commercial"


def test_unreadable_runner_json_is_kept_and_replaced(tmp_path):
    (tmp_path / "runner.json").write_text("{broken")
    state = st.RunnerState(tmp_path)
    assert state.notes and "could not be read" in state.notes[0]
    assert list(tmp_path.glob("runner.json.unreadable-*"))
    assert json.loads((tmp_path / "runner.json").read_text())["token"] == state.token


def test_data_environment_defaults_under_home_and_respects_the_user(tmp_path):
    home = tmp_path / "home"
    env = st.data_environment(home, {})
    assert env == {"BIOAGENT_DATA_LAKE": str((home / "data").resolve()),
                   "BIOAGENT_TCMDB": str((home / "data" / "tcmdb").resolve()),
                   "BIOAGENT_WORKSPACE": str((home / "workspace").resolve())}
    mine = {"BIOAGENT_DATA_LAKE": str(tmp_path / "lake")}
    env = st.data_environment(home, mine)
    assert env["BIOAGENT_DATA_LAKE"] == str(tmp_path / "lake")
    assert env["BIOAGENT_TCMDB"] == str(tmp_path / "lake" / "tcmdb")
    target: dict[str, str] = {"BIOAGENT_TCMDB": "/srv/tcmdb"}
    st.apply_environment(home, target)
    assert target["BIOAGENT_TCMDB"] == "/srv/tcmdb"
    assert target["BIOAGENT_DATA_LAKE"].endswith("data")
    paths = st.prepare_home(home)
    for key in ("jobs", "uploads", "projects", "data", "workspace", "cache"):
        assert paths[key].is_dir() and paths[key].is_absolute()


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    return parser.parse_args(list(argv))


def test_command_line_flags():
    a = _args("--port", "9000", "--allow-origin", "https://a.example", "--allow-origin",
              "https://b.example", "--allow-host", "llm.example.org", "--device", "cpu",
              "--threads", "2", "--max-jobs", "3", "--network", "--no-token", "--no-browser",
              "--quiet", "--web", "none")
    assert a.port == 9000 and a.allow_origin == ["https://a.example", "https://b.example"]
    assert a.allow_host == ["llm.example.org"] and a.device == "cpu" and a.threads == 2
    assert a.max_jobs == 3 and a.network and a.no_token and a.no_browser and a.quiet
    assert _args().host == "127.0.0.1" and _args().port == 8765


def test_refused_configurations_exit_before_binding(tmp_path, capsys):
    assert run(_args("--home", str(tmp_path), "--host", "0.0.0.0", "--no-token", "--port", "0",
                     "--no-browser", "--web", "none")) == 2
    assert "--no-token is refused" in capsys.readouterr().err
    assert run(_args("--home", str(tmp_path), "--allow-origin", "*", "--no-browser")) == 2
    assert "wildcard" in capsys.readouterr().err
    assert run(_args("--home", str(tmp_path), "--device", "gpu", "--port", "0", "--no-browser",
                     "--web", "none")) == 2
    assert "device must be" in capsys.readouterr().err
    assert run(_args("--home", str(tmp_path), "--allow-host", "*.example.org", "--port", "0",
                     "--no-browser", "--web", "none")) == 2
    assert "--allow-host" in capsys.readouterr().err
    assert run(_args("--home", str(tmp_path), "--web", str(tmp_path / "nowhere"), "--port", "0",
                     "--no-browser")) == 2
    assert "no index.html" in capsys.readouterr().err


def test_port_in_use_is_reported(tmp_path, capsys):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        assert run(_args("--home", str(tmp_path), "--port", str(port), "--no-browser",
                         "--web", "none")) == 1
        assert "cannot listen" in capsys.readouterr().err
    finally:
        sock.close()


def test_one_runner_per_home(tmp_path, capsys):
    from test_server_support import running_server
    home = tmp_path / "home"
    with running_server(home):
        with pytest.raises(st.HomeInUse), running_server(home):
            pass
        assert run(_args("--home", str(home), "--port", "0", "--no-browser",
                         "--web", "none")) == 1
        assert "another runner" in capsys.readouterr().err
    with running_server(home):                 # released when the first one stopped
        pass


def test_flags_are_saved_as_settings(tmp_path):
    from test_server_support import running_server
    with running_server(tmp_path / "home", threads=2, max_jobs=2, network=True,
                        device="cpu") as (srv, c):
        s = c.get("/api/settings")[1]
        assert s["threads"] == 2 and s["max_jobs"] == 2 and s["device"] == "cpu"
        assert s["network"]["enabled"] is True
    saved = json.loads((tmp_path / "home" / "runner.json").read_text())["settings"]
    assert saved["threads"] == 2 and saved["network"]["enabled"] is True


def _decode_pair(link: str) -> dict:
    import base64
    code = link.split("#pair=")[1]
    return json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))


def test_serve_as_a_process_prints_a_one_time_link_and_stops_cleanly(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BIOAGENT_")}
    log = tmp_path / "logs" / "runner.log"
    proc = subprocess.Popen(
        [sys.executable, "-m", "tcmstudio", "serve", "--port", "0", "--no-browser",
         "--home", str(tmp_path / "home"), "--web", "none", "--log", str(log)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    lines = []
    port = None
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            line = proc.stderr.readline()
            if not line:
                break
            lines.append(line)
            m = re.search(r"Local\s+http://127\.0\.0\.1:(\d+)/", line)
            if m:
                port = int(m.group(1))
            if "Ctrl+C" in line:                     # the banner's last line
                break
        banner = "".join(lines)
        assert port, banner
        # the link carries a one-time code, never the token (docs/V2.md §16)
        link = re.search(r"配对链接 Pair\s+(\S+)", banner).group(1)
        assert link.startswith("https://science.impf.ai/#pair=")
        doc = _decode_pair(link)
        assert doc == {"url": f"http://127.0.0.1:{port}", "code": doc["code"]}
        assert len(doc["code"]) >= 22
        token = json.loads((tmp_path / "home" / "runner.json").read_text())["token"]
        assert token not in banner and "--show-token" in banner
        assert "只能用一次" in banner and "one use" in banner
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/health")
        health = json.loads(conn.getresponse().read())
        conn.request("POST", "/api/pair/claim", body=json.dumps({"code": doc["code"]}),
                     headers={"Origin": "https://science.impf.ai",
                              "Content-Type": "application/json"})
        claimed = json.loads(conn.getresponse().read())
        conn.close()
        assert health["name"] == "tcmstudio" and health["token_required"] is True
        assert claimed == {"url": f"http://127.0.0.1:{port}", "token": token}
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(15) == 0
        assert "stopped" in proc.stderr.read()
        # --log: the banner, the pairing event and the request log went to the file as well
        text = log.read_text(encoding="utf-8")
        assert "配对链接 Pair" in text and "已配对 Paired" in text
        assert "POST /api/pair/claim 200" in text and "stopped" in text
        assert token not in text
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)


def test_show_token_prints_it_and_a_tokenless_runner_links_without_a_code(tmp_path):
    from tcmstudio.server import ServerConfig, _banner, make_server
    server = make_server(ServerConfig(home=tmp_path / "a", port=0, web=None, torch_probe=False,
                                      show_token=True))
    try:
        banner = _banner(server, {})
        assert server.guard.token in banner
        assert "code" in _decode_pair(re.search(r"Pair\s+(\S+)", banner).group(1))
    finally:
        server.close()
    server = make_server(ServerConfig(home=tmp_path / "b", port=0, web=None, torch_probe=False,
                                      no_token=True))
    try:
        banner = _banner(server, {})
        doc = _decode_pair(re.search(r"Pair\s+(\S+)", banner).group(1))
        assert doc == {"url": server.url, "token": ""}                # nothing secret to carry
        assert "not required" in banner
    finally:
        server.close()


def test_startup_errors_reach_the_log_file(tmp_path, capsys):
    log = tmp_path / "runner.log"
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    assert run(parser.parse_args(["--port", "70000", "--home", str(tmp_path / "h"),
                                  "--log", str(log)])) == 2
    assert "not a port number" in capsys.readouterr().err
    assert "not a port number" in log.read_text(encoding="utf-8")
