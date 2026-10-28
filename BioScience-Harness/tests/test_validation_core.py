"""bioagent.validation: statuses, records, reports, and probes that send real requests.

The probes are tested against a server on the loopback interface, which answers real HTTP:
a file, a missing file, a server error, a HEAD refusal and a redirect to another host name.
"""
from __future__ import annotations

import http.server
import socket
import threading
import urllib.error

import pytest

from bioagent.validation import (BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, UNVERIFIED,
                                 Check, Record, aggregate, load_report, report, write_report)
from bioagent.validation.environment import fingerprint
from bioagent.validation.network import classify, probe


def test_a_record_passes_only_when_every_applicable_check_passed():
    assert aggregate([PASS, PASS, NOT_APPLICABLE]) == PASS
    assert aggregate([PASS, SKIPPED]) == SKIPPED, "a skipped live check is not a pass"
    assert aggregate([PASS, UNVERIFIED, SKIPPED]) == UNVERIFIED
    assert aggregate([PASS, BLOCKED, UNVERIFIED]) == BLOCKED
    assert aggregate([BLOCKED, FAIL]) == FAIL
    assert aggregate([NOT_APPLICABLE]) == NOT_APPLICABLE
    assert aggregate([]) == NOT_APPLICABLE
    with pytest.raises(ValueError):
        Check("x", "OK")


def test_a_report_counts_records_and_checks_and_round_trips(tmp_path):
    a = Record("native.gc_content", "native", "1.0")
    a.add("invocation", PASS, "ran")
    a.add("functional_correctness", PASS, "matched", expected=0.5, got=0.5)
    b = Record("connector.x.y", "connector")
    b.add("invocation", BLOCKED, "the proxy refused x.org")
    doc = report("tool_validation_report", [a, b], tier="offline",
                 environment={"python": "3.12"})
    assert doc["summary"]["status"][PASS] == 1 and doc["summary"]["status"][BLOCKED] == 1
    assert doc["summary"]["checks"]["invocation"] == {PASS: 1, BLOCKED: 1}
    assert doc["records"][1]["failure_reason"] == "invocation: the proxy refused x.org"
    path = write_report(tmp_path / "r" / "report.json", doc)
    assert load_report(path)["records"][0]["checks"][1]["evidence"] == {"expected": 0.5, "got": 0.5}
    (tmp_path / "other.json").write_text('{"schema": "x"}')
    with pytest.raises(ValueError):
        load_report(tmp_path / "other.json")


def test_the_fingerprint_names_the_route_but_never_the_proxy_address(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.example:3128")
    env = fingerprint({"data": tmp_path / "not-yet"})
    assert env["network"]["proxy_configured"] is True
    assert "secret" not in repr(env) and "proxy.example" not in repr(env)
    assert env["disk"]["data"]["free_bytes"] > 0, "measured on the nearest existing parent"
    assert env["environment_digest"].startswith("sha256:")


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):                               # quiet
        pass

    def _answer(self, body: bool):
        port = self.server.server_address[1]
        if self.path == "/file.txt":
            data = b"0123456789"
            rng = self.headers.get("Range")
            if rng:
                self.send_response(206)
                self.send_header("Content-Range", f"bytes 0-0/{len(data)}")
                data = data[:1]
            else:
                self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if body:
                self.wfile.write(data)
        elif self.path == "/nohead.txt":
            if self.command == "HEAD":
                self.send_response(405)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", "bytes 0-0/42")
            self.send_header("Content-Length", "1")
            self.end_headers()
            self.wfile.write(b"x")
        elif self.path == "/down":
            self.send_response(503)
            self.end_headers()
        elif self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/file.txt")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_HEAD(self):
        self._answer(False)

    def do_GET(self):
        self._answer(True)


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_a_probe_reports_what_the_server_actually_said(server):
    ok = probe(f"{server}/file.txt")
    assert ok.status == PASS and ok.http_status == 200 and ok.size == 10 and ok.ranges is True
    nohead = probe(f"{server}/nohead.txt")
    assert nohead.status == PASS and nohead.method == "GET", "HEAD refused, GET asked instead"
    assert nohead.size == 42 and nohead.ranges is True
    gone = probe(f"{server}/missing.txt")
    assert gone.status == FAIL and "out of date" in gone.reason
    down = probe(f"{server}/down")
    assert down.status == BLOCKED and "HTTP 503" in down.reason
    moved = probe(f"{server}/moved")
    assert moved.status == FAIL and "another host" in moved.reason


def test_a_closed_port_is_blocked_not_failed():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    p = probe(f"http://127.0.0.1:{port}/x", timeout_s=5)
    assert p.status == BLOCKED and "refused" in p.reason


def test_classify_names_policy_dns_tls_and_rate_limits():
    tunnel = urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))
    status, reason = classify(tunnel, "bidd.group")
    assert status == BLOCKED and "proxy refused" in reason and "bidd.group" in reason
    dns = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    assert classify(dns, "x.org")[0] == BLOCKED and "resolve" in classify(dns, "x.org")[1]
    import ssl
    tls = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    assert "never turned off" in classify(tls, "x.org")[1]
    assert classify(TimeoutError("timed out"), "x.org", timeout_s=3)[1].endswith("(timeout)")
    for code, want in ((404, FAIL), (410, FAIL), (429, BLOCKED), (502, BLOCKED), (401, BLOCKED),
                       (400, FAIL)):
        err = urllib.error.HTTPError("https://x.org/f", code, "x", {}, None)
        assert classify(err, "x.org")[0] == want, code
    assert classify(ValueError("bad"), "x.org")[0] == FAIL


def _tool(path, script):
    path.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    path.chmod(0o755)


def test_a_binary_is_asked_for_its_version_the_way_it_answers(tmp_path, monkeypatch):
    # analysis job, ci 273: kallisto 0.48.0 reads --version as an unknown command, prints its
    # usage ("kallisto 0.48.0 ...") and exits 1; its version command is `kallisto version`
    from bioagent.validation.dependencies import readiness
    _tool(tmp_path / "kallisto", 'if [ "$1" = version ]; then echo "kallisto, version 0.48.0"; exit 0; fi\n'
                                 'echo "kallisto 0.48.0"; echo; echo "Usage: kallisto <CMD> [arguments] .."; exit 1\n')
    _tool(tmp_path / "salmon", 'echo "salmon: error while loading shared libraries" >&2; exit 127\n')
    monkeypatch.setenv("PATH", str(tmp_path))
    rec, = readiness({"groups": {"t": {"packages": {}, "binaries": ["kallisto", "salmon", "fastp"]}}})
    assert rec.check("kallisto:binary").status == PASS
    assert rec.check("kallisto:binary").detail == "kallisto, version 0.48.0"
    broken = rec.check("salmon:binary")
    assert broken.status == FAIL and "exited 127" in broken.detail and "shared libraries" in broken.detail
    assert rec.check("fastp:binary").status == BLOCKED


def test_readiness_checks_every_tool_the_rnaseq_pipeline_runs(tmp_path, monkeypatch):
    # `bioagent rnaseq --engine hisat2` is refused without hisat2-build, samtools and
    # featureCounts too; featureCounts reads --version as an unknown option and its version
    # option is -v
    from bioagent.omics import backends
    from bioagent.validation.dependencies import VERSION_ARGS, load_environments, readiness
    group = load_environments()["groups"]["transcriptomics"]
    assert set(backends.VERSION_ARGS) <= set(group["binaries"])
    for tool, args in backends.VERSION_ARGS.items():
        assert VERSION_ARGS.get(tool, ["--version"]) == args, tool
    _tool(tmp_path / "featureCounts", 'if [ "$1" = -v ]; then echo >&2; '
                                      'echo "featureCounts v2.0.6" >&2; exit 0; fi\n'
                                      'echo "Unrecognized option" >&2; exit 255\n')
    monkeypatch.setenv("PATH", str(tmp_path))
    rec, = readiness({"groups": {"t": {"packages": {}, "binaries": ["featureCounts"]}}})
    assert rec.check("featureCounts:binary").status == PASS
    assert rec.check("featureCounts:binary").detail == "featureCounts v2.0.6"
