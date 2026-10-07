"""MCP for real: a reviewed server, connected through the SDK, called on every path.

Every call here runs the official SDK, 1.x or 2.x, against a real server
(``mcp_fixture_server.py``, built with that SDK's own server class, over stdio, HTTP or
HTTPS). Nothing in the protocol is mocked, because what is being proved is that the
protocol's own answers become the right statuses. Four paths reach the server:

* the connection itself, which checks ``tools/list`` against the reviewed entry;
* ``Runtime.invoke`` through ``MCPBackend`` and the dispatcher;
* PSH's broker in process — through the bridge, and through ``MCPToolAdapter`` over the
  same connection;
* PSH's isolated child, which rebuilds the dispatcher from the registry file the bridge
  wrote, verifies its digest, and calls the server from a cleared environment.

On each path: success, ``isError``, a JSON-RPC error, timeout, no server, schema drift,
and a tool outside the allowlist. Over HTTPS: a certificate that does not verify, and a
redirect off the reviewed origin. The configuration tests need no SDK and run anywhere;
the one test that reaches a public server over the internet is marked ``integration``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

import pytest

from omics_world import need_mcp_sdk, need_module

from bioagent.backends.concrete import MCPBackend, MCPCallError, MCPReply
from bioagent.mcp import (MCPConfigError, MCPConnection, MCPDispatcher, MCPServerConfig,
                          MCPServerRegistry, ToolSnapshot, load_registry, review)
from bioagent.psh.assembly import default_runtime, mcp_dispatcher
from bioagent.runtime.agentspec import AgentSpec
from bioagent.runtime.component import ComponentManifest, LicenseSpec, RuntimeSpec
from bioagent.status import ExecutionStatus, LifecycleState

FIXTURE = Path(__file__).with_name("mcp_fixture_server.py")
ALLOWED = ("add", "change_echo", "echo", "environment", "exit_now", "fail",
           "needs_authorisation", "pid", "slow")
SPEC = AgentSpec(name="mcp-tests", permission_profile="biomedical-research")
PHI_TEXT = "Patient Alice Smith MRN 04851923 admitted with chest pain"
SHA = "a" * 64


def entry(**overrides):
    """A registry entry for the fixture, before review: tools are names, not digests."""
    raw = {"id": "fixture", "transport": "stdio", "command": sys.executable,
           "args": [str(FIXTURE)], "package": "bioagent-test-fixture", "version": "1.0",
           "destination": "public_remote", "call_timeout_s": 3, "connect_timeout_s": 30,
           "tools": list(ALLOWED)}
    raw.update(overrides)
    return raw


def snapshotted(**overrides):
    """An entry with placeholder digests: valid configuration, never connected."""
    raw = entry(**overrides)
    raw["tools"] = {name: {"input_schema": SHA} for name in raw["tools"]}
    return raw


def component(tool, server="fixture"):
    return ComponentManifest(
        id=f"mcp.{server}.{tool}", kind="tool", name=tool,
        description=f"the {tool} tool of the {server} MCP server",
        runtime=RuntimeSpec(backend="mcp", server=server, entrypoint=tool),
        license=LicenseSpec(spdx="MIT", integration_mode="federated"))


def gone(pid, wait_s=10.0):
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


@pytest.fixture(scope="module")
def marker(tmp_path_factory):
    return tmp_path_factory.mktemp("mcp") / "unreviewed-ran"


@pytest.fixture(scope="module")
def reviewed(marker):
    """The fixture server's entry, reviewed against the live server once per module."""
    need_mcp_sdk()
    config, listing = review(entry(args=[str(FIXTURE), "--marker", str(marker)]))
    assert set(listing) == set(ALLOWED)
    return config


@pytest.fixture(scope="module")
def drifted(reviewed):
    """The same reviewed snapshot, served by a build whose echo has changed since."""
    return dataclasses.replace(reviewed, id="fixture-drifted",
                               args=(*reviewed.args, "--drift"))


# ============================================ the reviewed configuration (no SDK)

def test_an_entry_round_trips_and_its_digest_names_exactly_what_was_admitted():
    config = MCPServerConfig.from_dict(snapshotted())
    assert MCPServerConfig.from_dict(config.to_dict()) == config
    assert MCPServerConfig.from_dict(snapshotted(call_timeout_s=3.0)).digest == config.digest
    assert config.allowlist == tuple(sorted(ALLOWED)) and config.credentials == ()
    changed = snapshotted()
    changed["tools"]["echo"]["output_schema"] = "b" * 64
    for different in (changed, snapshotted(call_timeout_s=4), snapshotted(note="re-read"),
                      snapshotted(destination="trusted_remote")):
        assert MCPServerConfig.from_dict(different).digest != config.digest


def test_a_malformed_entry_is_refused_with_every_reason():
    bad = snapshotted(env={"TOKEN": "ghp_0123456789abcdef", "LD_PRELOAD": "LIB"},
                      allowlist=["echo"], command="./server", destination="local_compute",
                      package="")
    with pytest.raises(MCPConfigError) as caught:
        MCPServerConfig.from_dict(bad)
    assert "unknown keys ['allowlist']" in str(caught.value)
    bad.pop("allowlist")
    with pytest.raises(MCPConfigError) as caught:
        MCPServerConfig.from_dict(bad)
    text = str(caught.value)
    for reason in ("must name a credential", "'LD_PRELOAD' is not a name", "bare name",
                   "never local", "package and version are required"):
        assert reason in text, (reason, text)
    remote = snapshotted(transport="streamable_http", url="http://user:pw@mcp.example.org/m",
                         command="", args=[])
    with pytest.raises(MCPConfigError, match="clear text") as caught:
        MCPServerConfig.from_dict(remote)
    assert "must not carry credentials" in str(caught.value)
    unsnapshotted = entry()                      # tools as bare names: never reviewed
    with pytest.raises(MCPConfigError, match="must map each allowlisted tool"):
        MCPServerConfig.from_dict(unsnapshotted)


def test_the_registry_refuses_bad_entries_and_duplicates_and_keeps_the_rest(tmp_path):
    good, twice = snapshotted(id="good"), snapshotted(id="twice")
    path = tmp_path / "mcp_servers.yaml"
    path.write_text(json.dumps({"api_version": "1", "servers": [
        good, twice, twice, snapshotted(id="bad", transport="carrier-pigeon")]}))
    registry = load_registry(path)
    assert list(registry.servers) == ["good"]
    assert dict(registry.refused) == {"bad": dict(registry.refused)["bad"],
                                      "twice": "declared more than once; no copy is admitted"}
    assert "carrier-pigeon" in registry.why_not("bad")
    assert "not in the reviewed MCP registry" in registry.why_not("ghost")
    with pytest.raises(MCPConfigError, match="api_version"):
        MCPServerRegistry.from_dict({"api_version": "2", "servers": []})
    with pytest.raises(MCPConfigError, match="no such registry file"):
        load_registry(tmp_path / "absent.yaml")


def test_the_shipped_registry_configures_nothing_so_nothing_changes(tmp_path):
    assert load_registry().empty
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, extra_manifests=(component("echo"),),
                              data_lake=tmp_path / "no-lake")
    backend = runtime.backends.get("mcp")
    assert backend.dispatcher is None and not backend.available()
    result = runtime.invoke("mcp.fixture.echo", spec=SPEC, text="hi")
    assert result.status is ExecutionStatus.UNAVAILABLE
    assert "no MCP dispatcher bound" in result.error
    assert MCPBackend().invoke(component("echo")).status is ExecutionStatus.RESOLVED


def test_an_unreadable_shipped_registry_refuses_every_server_rather_than_every_runtime(
        tmp_path, monkeypatch):
    import bioagent.mcp.config as config_module

    broken = tmp_path / "mcp_servers.yaml"
    broken.write_text("servers: {not: a list}\n")
    monkeypatch.setattr(config_module, "default_registry_path", lambda: broken)
    dispatcher = mcp_dispatcher()
    with pytest.raises(MCPCallError) as caught:
        dispatcher("fixture", "echo", text="hi")
    assert caught.value.status is ExecutionStatus.UNAVAILABLE
    assert "refused as a whole" in caught.value.reason
    with pytest.raises(MCPConfigError):           # a path asked for explicitly is an error
        mcp_dispatcher(broken)


def test_without_the_sdk_a_reviewed_server_is_unavailable_and_says_why(monkeypatch):
    monkeypatch.setitem(sys.modules, "mcp", None)             # the SDK is not importable
    registry = MCPServerRegistry([MCPServerConfig.from_dict(snapshotted())])
    result = MCPBackend(MCPDispatcher(registry)).invoke(component("echo"), text="hi")
    assert result.status is ExecutionStatus.UNAVAILABLE
    assert "pip install 'bioagent[mcp]'" in result.error
    assert result.metadata["server"] == "fixture"


def test_the_backend_maps_what_a_dispatcher_reports_to_statuses():
    m = component("echo")

    def raising(status):
        def dispatcher(server, tool, **kw):
            raise MCPCallError(f"{status.value} on purpose", status=status,
                               metadata={"tool": tool})
        return dispatcher

    for status in (ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT,
                   ExecutionStatus.UNAVAILABLE, ExecutionStatus.DENIED):
        result = MCPBackend(raising(status)).invoke(m)
        assert result.status is status and result.metadata["tool"] == "echo"
    # a dispatcher cannot raise its way to a success
    assert MCPBackend(raising(ExecutionStatus.SUCCEEDED)).invoke(m).status \
        is ExecutionStatus.FAILED
    error_reply = {"content": [{"type": "text", "text": "it broke"}], "isError": True}
    result = MCPBackend(lambda s, t, **k: error_reply).invoke(m)
    assert result.status is ExecutionStatus.FAILED and "it broke" in result.error
    wrapped = MCPReply({"content": [{"type": "text", "text": "x"}],
                        "structuredContent": {"result": "x"}, "isError": False},
                       metadata={"config_digest": "d"})
    result = MCPBackend(lambda s, t, **k: wrapped).invoke(m)
    assert result.status is ExecutionStatus.SUCCEEDED and result.value == {"result": "x"}
    assert result.metadata == {"connector": "fixture", "config_digest": "d"}


# ============================================ the connection, directly

def test_the_connection_admits_the_allowlist_and_hides_the_rest(reviewed, marker):
    with MCPConnection(reviewed) as connection:
        assert connection.tools == ALLOWED and connection.refused == {}
        assert connection.hidden == ("unreviewed",)
        assert [d["name"] for d in connection.descriptors()] == list(ALLOWED)
        assert connection.call_tool("echo", {"text": "hi"})["structuredContent"] == {
            "result": "hi"}
        assert connection.call_tool("add", {"a": 2, "b": 3})["structuredContent"] == {
            "sum": 5}
        failed = connection.call_tool("fail", {})
        assert failed["isError"] is True
        assert "failed on purpose" in failed["content"][0]["text"]
        with pytest.raises(MCPCallError) as caught:
            connection.call_tool("needs_authorisation", {})
        assert caught.value.status is ExecutionStatus.FAILED
        assert "protocol error -32042" in caught.value.reason
        with pytest.raises(MCPCallError) as caught:
            connection.call_tool("unreviewed", {})
        assert caught.value.status is ExecutionStatus.DENIED
        assert "not on the reviewed allowlist" in caught.value.reason
        started = time.monotonic()
        with pytest.raises(MCPCallError) as caught:
            connection.call_tool("slow", {"seconds": 30})
        assert caught.value.status is ExecutionStatus.TIMEOUT
        assert time.monotonic() - started < 15
        # a call that timed out does not take the session with it
        assert connection.call_tool("echo", {"text": "after"})["structuredContent"] == {
            "result": "after"}
    assert not marker.exists(), "a tool outside the allowlist ran"


def test_a_server_that_changed_since_review_is_refused_with_the_drift_named(drifted):
    retired = dataclasses.replace(
        drifted, tools={**drifted.tools, "retired": ToolSnapshot(input_schema=SHA)})
    with MCPConnection(retired) as connection:
        assert "echo" not in connection.tools and "add" in connection.tools
        with pytest.raises(MCPCallError) as caught:
            connection.call_tool("echo", {"text": "hi"})
        assert caught.value.status is ExecutionStatus.DENIED
        assert "drifted from the reviewed snapshot: inputSchema sha256" in caught.value.reason
        with pytest.raises(MCPCallError) as caught:
            connection.call_tool("retired", {})
        assert caught.value.status is ExecutionStatus.UNAVAILABLE
        assert "the tool is missing" in caught.value.reason


def test_a_tool_list_change_mid_session_is_checked_before_the_next_call(reviewed):
    with MCPConnection(reviewed) as connection:
        assert connection.call_tool("echo", {"text": "a"})["isError"] is False
        assert connection.call_tool("change_echo", {})["structuredContent"] == {
            "result": "changed"}
        with pytest.raises(MCPCallError) as caught:
            connection.call_tool("echo", {"text": "a"})
        assert caught.value.status is ExecutionStatus.DENIED
        assert "drifted" in caught.value.reason


def test_closing_stops_the_server_and_so_does_interpreter_exit(reviewed, tmp_path):
    # A server that ignores the end of its input: closing stdin alone would leave it
    # running, so only the termination that follows can make these pass.
    lingering = dataclasses.replace(reviewed, args=(*reviewed.args, "--linger", "120"))
    connection = MCPConnection(lingering).open()
    pid = connection.call_tool("pid", {})["structuredContent"]["result"]
    connection.close()
    assert gone(pid), f"server {pid} outlived close()"
    with pytest.raises(MCPCallError, match="has ended"):
        connection.call_tool("echo", {"text": "x"})

    registry = MCPServerRegistry([lingering]).write(tmp_path / "servers.json")
    script = ("import sys\nfrom bioagent.mcp import MCPConnection, load_registry\n"
              "c = MCPConnection(load_registry(sys.argv[1]).get('fixture')).open()\n"
              "print(c.call_tool('pid', {})['structuredContent']['result'], flush=True)\n")
    src = str(Path(__import__("bioagent").__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [src, *filter(None, [os.environ.get("PYTHONPATH")])])}
    done = subprocess.run([sys.executable, "-c", script, str(registry)], env=env,
                          capture_output=True, text=True, timeout=120, check=True)
    assert gone(int(done.stdout.strip())), "a connection left open outlived its interpreter"


def test_a_server_that_dies_is_reported_and_replaced(reviewed):
    dispatcher = MCPDispatcher(MCPServerRegistry([reviewed]))
    try:
        first = dispatcher("fixture", "pid").value["structuredContent"]["result"]
        with pytest.raises(MCPCallError) as caught:
            dispatcher("fixture", "exit_now")
        assert caught.value.status is ExecutionStatus.FAILED
        assert "closed the connection while 'exit_now' was running" in caught.value.reason
        second = dispatcher("fixture", "pid").value["structuredContent"]["result"]
        assert second != first and gone(first)
    finally:
        dispatcher.close()


def test_a_server_that_cannot_start_is_unavailable_in_its_own_words(reviewed):
    missing = dataclasses.replace(reviewed, command="/nonexistent/mcp-server", args=())
    with pytest.raises(MCPCallError) as caught:
        MCPConnection(missing).open()
    assert caught.value.status is ExecutionStatus.UNAVAILABLE
    assert "could not be started" in caught.value.reason
    crashing = dataclasses.replace(reviewed, args=(
        "-c", "import sys; sys.stderr.write('no module named fixture_dep'); sys.exit(3)"))
    with pytest.raises(MCPCallError) as caught:
        MCPConnection(crashing).open()
    assert caught.value.status is ExecutionStatus.UNAVAILABLE
    assert "no module named fixture_dep" in caught.value.reason


@contextlib.contextmanager
def served_over_http(*args):
    """The fixture served over streamable HTTP on loopback; yields its port."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen([sys.executable, str(FIXTURE), "--http", str(port), *args],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                if time.monotonic() > deadline or server.poll() is not None:
                    pytest.fail("the streamable HTTP fixture did not start")
                time.sleep(0.2)
        yield port
    finally:
        server.terminate()
        server.wait(10)


@pytest.fixture
def http_fixture(reviewed):
    with served_over_http() as port:
        yield port


def reached_at(reviewed, url):
    """The reviewed entry, for the same server reached at ``url`` instead of started."""
    raw = {k: v for k, v in reviewed.to_dict().items() if k not in ("command", "args")}
    return MCPServerConfig.from_dict({**raw, "id": "remote", "transport": "streamable_http",
                                      "url": url})


@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    """Loopback certificates: one from a CA the client is given, one from that CA for
    another name, one from a CA it is not given. Each is a (certificate, key) pair of
    files; ``ca`` is the trusted CA's certificate file."""
    need_module("cryptography")
    import datetime
    import ipaddress
    from types import SimpleNamespace

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    root = tmp_path_factory.mktemp("tls")
    now = datetime.datetime.now(datetime.timezone.utc)

    def issue(subject, ca=None, names=()):
        """A key and certificate: a self-signed CA without ``ca``, else a server's."""
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
        signer, issuer = ca or (key, None)
        usage = dict.fromkeys(("content_commitment", "key_encipherment", "data_encipherment",
                               "key_agreement", "encipher_only", "decipher_only"), False)
        built = (x509.CertificateBuilder().subject_name(name)
                 .issuer_name(issuer.subject if issuer else name)
                 .public_key(key.public_key()).serial_number(x509.random_serial_number())
                 .not_valid_before(now - datetime.timedelta(minutes=5))
                 .not_valid_after(now + datetime.timedelta(days=1))
                 .add_extension(x509.BasicConstraints(ca=ca is None, path_length=None), True)
                 .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=ca is None,
                                              crl_sign=ca is None, **usage), True)
                 .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                                False))
        if ca:
            built = (built.add_extension(x509.SubjectAlternativeName(list(names)), False)
                     .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                                    False)
                     .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                         signer.public_key()), False))
        return key, built.sign(signer, hashes.SHA256())

    def files(stem, key, cert):
        pem = root / f"{stem}.pem"
        pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private = root / f"{stem}.key"
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        return str(pem), str(private)

    loopback = (x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost"))
    trusted, stranger = issue("bioagent test CA"), issue("a CA nobody gave the client")
    return SimpleNamespace(
        ca=files("ca", *trusted)[0],
        good=files("good", *issue("fixture", trusted, loopback)),
        other_name=files("other", *issue("fixture", trusted,
                                         (x509.DNSName("elsewhere.invalid"),))),
        untrusted=files("untrusted", *issue("fixture", stranger, loopback)))


def test_a_streamable_http_server_is_checked_against_the_same_snapshot(reviewed,
                                                                      http_fixture):
    remote = reached_at(reviewed, f"http://127.0.0.1:{http_fixture}/mcp")
    with MCPConnection(remote) as connection:
        # reviewed over stdio, verified over HTTP: the snapshot pins the tools, not the pipe
        assert connection.tools == ALLOWED and connection.hidden == ("unreviewed",)
        assert connection.call_tool("add", {"a": 2, "b": 3})["structuredContent"] == {
            "sum": 5}
        with pytest.raises(MCPCallError) as caught:
            connection.call_tool("slow", {"seconds": 30})
        assert caught.value.status is ExecutionStatus.TIMEOUT
        with pytest.raises(MCPCallError) as caught:
            connection.call_tool("unreviewed", {})
        assert caught.value.status is ExecutionStatus.DENIED
    unreachable = dataclasses.replace(remote, url="http://127.0.0.1:9/mcp")
    with pytest.raises(MCPCallError) as caught:
        MCPConnection(unreachable).open()
    assert caught.value.status is ExecutionStatus.UNAVAILABLE
    assert "cannot connect" in caught.value.reason


def test_a_server_over_https_is_verified_and_one_that_does_not_verify_is_refused(
        reviewed, tls, monkeypatch):
    # The client is given the CA the way a deployment gives it one: SSL_CERT_FILE, one of
    # the CA variables the transport also hands to the servers it starts.
    monkeypatch.setenv("SSL_CERT_FILE", tls.ca)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    with served_over_http("--tls", *tls.good) as port:
        for host in ("127.0.0.1", "localhost"):
            with MCPConnection(reached_at(reviewed, f"https://{host}:{port}/mcp")) as remote:
                assert remote.tools == ALLOWED and remote.hidden == ("unreviewed",)
                assert remote.call_tool("add", {"a": 2, "b": 3})["structuredContent"] == {
                    "sum": 5}
    # Never accepted instead: a certificate for another name, and one from a CA the client
    # was not given. Each is refused before anything is sent, in OpenSSL's words.
    for certificate, why in ((tls.other_name, "IP address mismatch"),
                             (tls.untrusted, "unable to get local issuer certificate")):
        with served_over_http("--tls", *certificate) as port:
            with pytest.raises(MCPCallError) as caught:
                MCPConnection(reached_at(reviewed, f"https://127.0.0.1:{port}/mcp")).open()
        assert caught.value.status is ExecutionStatus.UNAVAILABLE
        assert "CERTIFICATE_VERIFY_FAILED" in caught.value.reason, caught.value.reason
        assert why in caught.value.reason
    # and no entry can ask for the check to be skipped
    https = reached_at(reviewed, "https://127.0.0.1/mcp").to_dict()
    with pytest.raises(MCPConfigError, match=r"unknown keys \['verify'\]"):
        MCPServerConfig.from_dict({**https, "verify": False})


@contextlib.contextmanager
def redirecting_to(location):
    """A loopback HTTP server that answers every request with a 307 to ``location``."""
    import http.server
    import threading

    class Redirect(http.server.BaseHTTPRequestHandler):
        def answer(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(307)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = do_DELETE = answer

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def test_a_redirect_off_the_reviewed_origin_is_never_followed(reviewed, http_fixture):
    # The redirect leads to a working server: had the client followed it (as the 1.29 SDK
    # would, to plain http on any host), the connection would have opened there.
    with redirecting_to(f"http://127.0.0.1:{http_fixture}/mcp") as port:
        with pytest.raises(MCPCallError) as caught:
            MCPConnection(reached_at(reviewed, f"http://127.0.0.1:{port}/mcp")).open()
    assert caught.value.status is ExecutionStatus.UNAVAILABLE
    assert "Redirect" in caught.value.reason, caught.value.reason


def test_an_sdk_major_the_transport_does_not_know_is_refused_before_anything_starts(
        monkeypatch):
    from bioagent.mcp import connection as connection_module

    monkeypatch.setattr(connection_module, "_sdk_version", lambda: "3.0.0")
    # a command that cannot start: had the check let it through, the reason would say so
    config = MCPServerConfig.from_dict(snapshotted(command="/nonexistent/mcp-server",
                                                   args=[]))
    with pytest.raises(MCPCallError) as caught:
        MCPConnection(config).open()
    assert caught.value.status is ExecutionStatus.UNAVAILABLE
    assert "3.0.0" in caught.value.reason and "mcp>=1.29,<3" in caught.value.reason


def test_a_reviewed_setting_and_the_ca_bundle_reach_the_server(reviewed, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
    configured = dataclasses.replace(reviewed, settings={"FIXTURE_SETTING": "/run/cache"})
    with MCPConnection(configured) as connection:
        seen = connection.call_tool("environment")["structuredContent"]
    assert seen["setting"] == "/run/cache"
    assert seen["ca_bundle"] == "/etc/ssl/certs/ca-certificates.crt"
    assert configured.digest != reviewed.digest                  # the setting is admitted


@pytest.mark.parametrize("settings, why", [
    ({"SSL_CERT_FILE": "/tmp/mine.pem"}, "not a name a reviewed entry may set"),
    ({"HTTPS_PROXY": "http://elsewhere:3128"}, "not a name a reviewed entry may set"),
    ({"NODE_TLS_REJECT_UNAUTHORIZED": "0"}, "not a name a reviewed entry may set"),
    ({"SSLKEYLOGFILE": "/tmp/keys.log"}, "not a name a reviewed entry may set"),
    ({"API_TOKEN": "ghp_" + "x" * 36}, "looks like a credential"),
    ({"API_TOKEN": "Zm9vYmFyYmF6cXV4cXV1eGNvcmdlZ3JhdWx0"}, "looks like a credential"),
    ({"CACHE": "a\nb"}, "one line"),
])
def test_a_setting_is_never_a_route_a_trust_root_or_a_secret(settings, why):
    with pytest.raises(MCPConfigError, match=why):
        MCPServerConfig.from_dict(snapshotted(settings=settings))


def test_a_path_setting_may_carry_a_digest():
    config = MCPServerConfig.from_dict(snapshotted(settings={"CACHE": "/runs/" + SHA}))
    assert MCPServerConfig.from_dict(config.to_dict()) == config


def test_credentials_are_resolved_by_name_and_never_echoed(reviewed):
    credentialed = dataclasses.replace(reviewed, env={"FIXTURE_TOKEN": "FIXTURE_TOKEN_NAME"})
    granted = MCPDispatcher(MCPServerRegistry([credentialed]),
                            credentials={"FIXTURE_TOKEN_NAME": "s3cret-value"}.get)
    try:
        reply = granted("fixture", "environment").value
        assert reply["structuredContent"]["token"] == "set"
    finally:
        granted.close()
    for credentials, why in ((None, "holds no credential source"),
                             ({}.get, "FIXTURE_TOKEN_NAME for MCP server")):
        with pytest.raises(MCPCallError) as caught:
            MCPDispatcher(MCPServerRegistry([credentialed]), credentials=credentials)(
                "fixture", "environment")
        assert caught.value.status is ExecutionStatus.UNAVAILABLE
        assert why in caught.value.reason and "s3cret" not in caught.value.reason


def test_an_operator_reviews_a_draft_reads_it_and_checks_the_registry(reviewed, tmp_path,
                                                                     capsys):
    import yaml

    from bioagent.mcp.__main__ import main

    draft = tmp_path / "draft.yaml"
    draft.write_text(yaml.safe_dump(entry(tools=["echo", "add"])))
    assert main(["review", str(draft)]) == 0
    printed = capsys.readouterr().out
    # the person committing the entry is shown what they vouch for, not only digests
    assert "# --- echo" in printed and "Return the text unchanged." in printed
    registry_file = tmp_path / "mcp_servers.yaml"
    registry_file.write_text(yaml.safe_dump({"api_version": "1",
                                             "servers": yaml.safe_load(printed)}))
    assert load_registry(registry_file).get("fixture").tools["echo"] == \
        reviewed.tools["echo"]
    assert main(["check", str(registry_file)]) == 0
    registry_file.write_text(yaml.safe_dump({"api_version": "1", "servers": [
        *yaml.safe_load(printed), {"id": "Not An Id"}]}))
    assert main(["check", str(registry_file)]) == 1
    assert "REFUSED Not An Id" in capsys.readouterr().err


# ============================================ Runtime.invoke through MCPBackend

def test_runtime_invoke_reaches_the_server_and_reports_each_outcome(reviewed, drifted,
                                                                    marker, tmp_path):
    tools = ("echo", "add", "fail", "slow", "needs_authorisation", "unreviewed")
    manifests = [component(t) for t in tools] + [component("echo", "ghost"),
                                                 component("echo", "fixture-drifted")]
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, extra_manifests=manifests,
                              data_lake=tmp_path / "no-lake",
                              mcp_servers=MCPServerRegistry([reviewed, drifted]))
    try:
        echo = runtime.invoke("mcp.fixture.echo", spec=SPEC, text="hello")
        assert echo.status is ExecutionStatus.SUCCEEDED and echo.value == {"result": "hello"}
        assert echo.metadata["schema_digest"] == reviewed.tools["echo"].input_schema
        assert echo.metadata["config_digest"] == reviewed.digest
        assert (echo.metadata["server"], echo.metadata["tool"]) == ("fixture", "echo")
        assert echo.metadata["server_version"] == "1.0"
        # which SDK ran the session: the two majors keep different parts of a reply
        assert echo.metadata["client_sdk_version"] == metadata.version("mcp")
        assert runtime.registry.get("mcp.fixture.echo").state is LifecycleState.READY
        assert runtime.invoke("mcp.fixture.add", spec=SPEC, a=2, b=3).value == {"sum": 5}

        outcomes = {
            "mcp.fixture.fail": (ExecutionStatus.FAILED, "failed on purpose"),
            "mcp.fixture.slow": (ExecutionStatus.TIMEOUT, "did not answer within 3s"),
            "mcp.fixture.needs_authorisation": (ExecutionStatus.FAILED, "protocol error"),
            "mcp.fixture.unreviewed": (ExecutionStatus.DENIED, "reviewed allowlist"),
            "mcp.ghost.echo": (ExecutionStatus.UNAVAILABLE, "not in the reviewed MCP"),
            "mcp.fixture-drifted.echo": (ExecutionStatus.DENIED, "inputSchema sha256"),
        }
        for cid, (status, why) in outcomes.items():
            kwargs = {"seconds": 30} if cid.endswith("slow") else {}
            if cid.endswith("echo"):
                kwargs = {"text": "hello"}
            result = runtime.invoke(cid, spec=SPEC, **kwargs)
            assert result.status is status and why in result.error, (cid, result)
    finally:
        runtime.backends.get("mcp").close()
    assert not marker.exists()


# ============================================ PSH: the broker, in process

def psh_kernel(state_dir):
    pytest.importorskip("psh")
    from psh.config import PSHConfig
    from psh.contracts import Autonomy, RiskTier
    from psh.kernel import TrustedKernel
    from psh.labels import Destination, Sensitivity
    from psh.policy import PolicySnapshot

    policy = PolicySnapshot(
        profile_id="mcp_bench", max_data_label=Sensitivity.PHI, autonomy=Autonomy.ACT,
        risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False,
        allowed_destinations=(Destination.LOCAL_COMPUTE, Destination.USER_OUTPUT,
                              Destination.PERSISTENT, Destination.PUBLIC_REMOTE))
    return TrustedKernel(PSHConfig(state_dir=state_dir).ensure_dirs(), policy=policy)


def test_the_bridge_admits_an_mcp_component_as_a_remote_mutating_tool():
    pytest.importorskip("psh")
    from psh.contracts import RiskTier
    from psh.labels import Destination, Sensitivity

    from bioagent.psh import bridge_manifest

    config = MCPServerConfig.from_dict(snapshotted())
    public = bridge_manifest(component("echo"), mcp_server=config)
    assert public.destinations == (Destination.PUBLIC_REMOTE,)
    assert public.max_label is Sensitivity.RESEARCH_DEIDENTIFIED
    assert public.mutates and public.risk_tier is RiskTier.R2_CONSEQUENTIAL
    assert public.requires_network and public.timeout_s >= 30 + 3
    assert public.provenance["mcp_config_digest"] == config.digest
    trusted = dataclasses.replace(config, destination="trusted_remote")
    assert bridge_manifest(component("echo"), mcp_server=trusted).destinations == (
        Destination.TRUSTED_REMOTE,)
    unknown = bridge_manifest(component("echo"))
    assert unknown.destinations == (Destination.PUBLIC_REMOTE,)
    assert unknown.provenance["mcp_config"].startswith("no reviewed entry")


def test_the_broker_calls_the_server_in_process_and_types_each_failure(reviewed, drifted,
                                                                      tmp_path):
    from psh.contracts import (CapabilityUnavailable, ContractViolation, EgressDenied,
                               PolicyDenied, ToolTimeout)

    from bioagent.psh import BioScienceBridge

    kernel = psh_kernel(tmp_path / "k")
    tools = ("echo", "fail", "slow", "unreviewed")
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, data_lake=tmp_path / "no-lake",
                              extra_manifests=[component(t) for t in tools]
                              + [component("echo", "ghost"),
                                 component("echo", "fixture-drifted")],
                              mcp_servers=MCPServerRegistry([reviewed, drifted]))
    try:
        bridge = BioScienceBridge(kernel, runtime, isolate=False)
        for manifest in runtime.registry:
            bridge.admit(manifest)
        envelope = kernel.policy.envelope()
        call = kernel.broker.call_tool
        assert call(bridge.component("mcp.fixture.echo"), {"text": "hi"},
                    envelope).value == {"result": "hi"}
        with pytest.raises(ContractViolation, match="failed on purpose"):
            call(bridge.component("mcp.fixture.fail"), {}, envelope)
        with pytest.raises(ToolTimeout):
            call(bridge.component("mcp.fixture.slow"), {"seconds": 30}, envelope)
        with pytest.raises(PolicyDenied, match="reviewed allowlist"):
            call(bridge.component("mcp.fixture.unreviewed"), {}, envelope)
        with pytest.raises(CapabilityUnavailable, match="not in the reviewed MCP registry"):
            call(bridge.component("mcp.ghost.echo"), {"text": "hi"}, envelope)
        with pytest.raises(PolicyDenied, match="drifted from the reviewed snapshot"):
            call(bridge.component("mcp.fixture-drifted.echo"), {"text": "hi"}, envelope)
        echo = bridge.component("mcp.fixture.echo")
        before = echo.calls
        with pytest.raises(EgressDenied):           # PHI never reaches a public server
            call(echo, {"text": PHI_TEXT}, envelope)
        assert echo.calls == before
        admitted = [e for e in kernel.events.records()
                    if e.event_type == "bioscience_component_admitted"
                    and e.component_id == "mcp.fixture.echo"]
        assert admitted and admitted[0].detail["mcp_config_digest"] == reviewed.digest
    finally:
        runtime.backends.get("mcp").close()
        kernel.close()


def test_mcp_tool_adapter_admits_only_verified_tools_over_the_same_connection(
        reviewed, drifted, tmp_path):
    from psh.contracts import CapabilityUnavailable, ContractViolation, ToolTimeout

    from bioagent.psh import admit_mcp_server

    kernel = psh_kernel(tmp_path / "k")
    connection = MCPConnection(reviewed)
    try:
        adapter = admit_mcp_server(kernel, connection)
        assert set(adapter.admitted) == {f"mcp.fixture.{t}" for t in ALLOWED}
        envelope = kernel.policy.envelope()
        add = adapter.admitted["mcp.fixture.add"]
        assert kernel.broker.call_tool(add, {"a": 2, "b": 3}, envelope).value == {"sum": 5}
        with pytest.raises(ContractViolation, match="failed on purpose"):
            kernel.broker.call_tool(adapter.admitted["mcp.fixture.fail"], {}, envelope)
        with pytest.raises(ToolTimeout):
            kernel.broker.call_tool(adapter.admitted["mcp.fixture.slow"], {"seconds": 30},
                                    envelope)
        with MCPConnection(drifted) as other:
            changed = admit_mcp_server(kernel, other)
            assert "mcp.fixture-drifted.echo" not in changed.admitted
            assert "mcp.fixture-drifted.add" in changed.admitted
        absent = dataclasses.replace(reviewed, command="/nonexistent/mcp-server", args=())
        with pytest.raises(CapabilityUnavailable, match="could not be started"):
            admit_mcp_server(kernel, MCPConnection(absent))
    finally:
        connection.close()
        kernel.close()


# ============================================ PSH: the isolated child

@pytest.fixture
def isolated(reviewed, drifted, tmp_path):
    """A kernel, and a bridge that runs every MCP component in a kernel child process."""
    from bioagent.psh import BioScienceBridge

    kernel = psh_kernel(tmp_path / "k")
    tools = ("echo", "fail", "slow", "unreviewed", "environment")
    manifests = [component(t) for t in tools] + [component("echo", "ghost"),
                                                 component("echo", "fixture-drifted")]
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, data_lake=tmp_path / "no-lake",
                              extra_manifests=manifests,
                              mcp_servers=MCPServerRegistry([reviewed, drifted]))
    bridge = BioScienceBridge(kernel, runtime, isolate=True)
    for manifest in manifests:
        bridge.admit(manifest)
    yield kernel, bridge
    kernel.close()


def test_the_isolated_child_rebuilds_the_dispatcher_and_calls_the_server(isolated, reviewed):
    kernel, bridge = isolated
    echo = bridge.component("mcp.fixture.echo")
    argv = shlex.split(echo.manifest.entrypoint)
    written = Path(argv[argv.index("--mcp-config") + 1])
    child = load_registry(written)
    assert list(child.servers) == ["fixture"], "the child holds only the server it calls"
    assert argv[argv.index("--mcp-config-digest") + 1] == child.digest
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert child.get("fixture").digest == reviewed.digest

    before = kernel.broker.stats()["isolated_tool_calls"]
    result = kernel.broker.call_tool(echo, {"text": "from the child"},
                                     kernel.policy.envelope())
    assert result.value == {"result": "from the child"}
    assert kernel.broker.stats()["isolated_tool_calls"] == before + 1
    assert echo.calls == 0, "the parent process never ran it"
    # The server was handed the kernel's egress proxy, not this process's route.
    seen = kernel.broker.call_tool(bridge.component("mcp.fixture.environment"), {},
                                   kernel.policy.envelope()).value
    assert seen["https_proxy"].startswith("http://127.0.0.1:")
    assert seen["https_proxy"] != os.environ.get("HTTPS_PROXY", "")


def test_the_isolated_child_reports_each_failure_as_the_kernel_reads_it(isolated, marker):
    from psh.contracts import ContractViolation, ToolTimeout

    kernel, bridge = isolated
    envelope = kernel.policy.envelope()
    call = kernel.broker.call_tool
    with pytest.raises(ContractViolation, match="FAILED: .*failed on purpose"):
        call(bridge.component("mcp.fixture.fail"), {}, envelope)
    with pytest.raises(ToolTimeout, match="TIMEOUT"):
        call(bridge.component("mcp.fixture.slow"), {"seconds": 30}, envelope)
    with pytest.raises(ContractViolation, match="DENIED: .*reviewed allowlist"):
        call(bridge.component("mcp.fixture.unreviewed"), {}, envelope)
    with pytest.raises(ContractViolation, match="UNAVAILABLE: .*not in the reviewed MCP"):
        call(bridge.component("mcp.ghost.echo"), {"text": "hi"}, envelope)
    with pytest.raises(ContractViolation, match="DENIED: .*drifted"):
        call(bridge.component("mcp.fixture-drifted.echo"), {"text": "hi"}, envelope)
    assert not marker.exists()


def test_a_tampered_registry_file_is_refused_by_the_child(isolated):
    from psh.contracts import ContractViolation

    kernel, bridge = isolated
    echo = bridge.component("mcp.fixture.echo")
    argv = shlex.split(echo.manifest.entrypoint)
    written = Path(argv[argv.index("--mcp-config") + 1])
    content = json.loads(written.read_text())
    content["servers"][0]["call_timeout_s"] = 600.0
    written.write_text(json.dumps(content))
    with pytest.raises(ContractViolation, match="not the .* it was admitted under"):
        kernel.broker.call_tool(echo, {"text": "hi"}, kernel.policy.envelope())


def test_a_credentialed_server_runs_in_process_and_never_in_the_child(reviewed, tmp_path,
                                                                     monkeypatch, capsys):
    from bioagent.psh import BioScienceBridge, BridgeRefused
    from bioagent.psh import exec as isolated_entrypoint

    credentialed = dataclasses.replace(reviewed, env={"FIXTURE_TOKEN": "FIXTURE_TOKEN_NAME"})
    monkeypatch.setenv("FIXTURE_TOKEN_NAME", "s3cret-value")
    kernel = psh_kernel(tmp_path / "k")
    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, data_lake=tmp_path / "no-lake",
                              extra_manifests=[component("environment")],
                              mcp_servers=MCPServerRegistry([credentialed]))
    try:
        with pytest.raises(BridgeRefused, match="needs credentials"):
            BioScienceBridge(kernel, runtime, isolate=True).admit(component("environment"))
        in_process = BioScienceBridge(kernel, runtime, isolate=False)
        in_process.admit(component("environment"))
        seen = kernel.broker.call_tool(in_process.component("mcp.fixture.environment"), {},
                                       kernel.policy.envelope()).value
        assert seen["token"] == "set"
    finally:
        runtime.backends.get("mcp").close()
        kernel.close()

    # The child refuses it even with the credential in its own environment: it resolves
    # no credential from anywhere.
    registry = MCPServerRegistry([credentialed])
    path = registry.write(tmp_path / "child.mcp.json")
    manifest = component("environment").save(tmp_path / "environment.yaml")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(
        {"tool": "mcp.fixture.environment", "run_id": "r", "payload": {}})))
    code = isolated_entrypoint.main(["--manifest", str(manifest), "--mcp-config", str(path),
                                     "--mcp-config-digest", registry.digest])
    err = capsys.readouterr().err
    assert code == 1 and "UNAVAILABLE" in err and "holds no credential source" in err
    assert "s3cret" not in err


# ============================================ a public server, over the internet

#: DeepWiki's public MCP server: HTTPS, no credentials. Only its read-only listing tool is
#: allowlisted; the other two stay hidden (one of them puts a question to a model).
DEEPWIKI = {"id": "deepwiki", "transport": "streamable_http",
            "url": "https://mcp.deepwiki.com/mcp", "destination": "public_remote",
            "package": "deepwiki-mcp", "version": "2.14.3", "connect_timeout_s": 60,
            "call_timeout_s": 120, "tools": ["read_wiki_structure"]}
REPO = "modelcontextprotocol/python-sdk"


@pytest.mark.integration
def test_a_public_server_is_reviewed_checked_and_called_over_https(tmp_path, capsys):
    import yaml

    from bioagent.mcp.__main__ import main

    need_mcp_sdk()
    draft = tmp_path / "deepwiki.yaml"
    draft.write_text(yaml.safe_dump(DEEPWIKI))
    assert main(["review", str(draft)]) == 0, capsys.readouterr().err
    registry_file = tmp_path / "mcp_servers.yaml"
    registry_file.write_text(yaml.safe_dump({"api_version": "1", "servers": yaml.safe_load(
        capsys.readouterr().out)}))
    assert main(["check", str(registry_file)]) == 0
    registry = load_registry(registry_file)
    config = registry.get("deepwiki")

    with MCPConnection(config) as connection:
        assert connection.tools == ("read_wiki_structure",)
        assert "ask_wiki_question" in connection.hidden
        reply = connection.call_tool("read_wiki_structure", {"repoName": REPO})
        assert reply["isError"] is False and REPO in reply["structuredContent"]["result"]
        with pytest.raises(MCPCallError) as caught:            # hidden: never sent
            connection.call_tool("ask_wiki_question", {"repoName": REPO, "question": "?"})
        assert caught.value.status is ExecutionStatus.DENIED

    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              skills=False, data_lake=tmp_path / "no-lake",
                              extra_manifests=[component("read_wiki_structure", "deepwiki")],
                              mcp_servers=registry)
    try:
        result = runtime.invoke("mcp.deepwiki.read_wiki_structure", spec=SPEC, repoName=REPO)
    finally:
        runtime.backends.get("mcp").close()
    assert result.status is ExecutionStatus.SUCCEEDED, result.error
    assert REPO in result.value["result"]
    assert result.metadata["config_digest"] == config.digest
    assert result.metadata["server_reported"]["name"] == "DeepWiki"

    # The same live server against a snapshot it no longer matches: refused, part named.
    snapshot = config.tools["read_wiki_structure"]
    stale = dataclasses.replace(config, tools={"read_wiki_structure": dataclasses.replace(
        snapshot, description="0" * 64)})
    with MCPConnection(stale) as connection:
        with pytest.raises(MCPCallError) as caught:
            connection.call_tool("read_wiki_structure", {"repoName": REPO})
    assert caught.value.status is ExecutionStatus.DENIED
    assert "description sha256 reviewed 000000000000" in caught.value.reason
