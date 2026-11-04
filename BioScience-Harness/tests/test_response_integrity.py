"""An answer is a result only if it is data of the shape the operation returns.

Audit of 2026-11-04 (P0): SIGNOR's ``getData.php?type=connect`` answered its registered
four-accession example with HTTP 200, ``text/plain`` and a PHP fatal error. The HTTP
backend parsed the error text as "XML" and the dispatcher reported the call succeeded.
These tests hold the repair: an error report or a stack trace is FAILED before it is
parsed, and an operation that declares its answer's shape (SIGNOR's relation table: both
endpoint entities, the effect and a SIGNOR id in every row) refuses an answer that does
not fit — malformed, misaligned or empty — so none of them can reach evidence synthesis.

Every test runs offline against a server on 127.0.0.1. The SIGNOR rows are synthetic, in
SIGNOR's documented column layout (the ids ``SIGNOR-9000xx`` are not SIGNOR's).
"""

from __future__ import annotations

import gzip
import http.server
import json
import threading

import pytest

from bioagent.backends.http import (HTTPBackend, HTTPRequest, _contract_problems,
                                    _error_report)
from bioagent.providers.public_apis import BY_KEY, contract_sample, render_call, table_contract
from bioagent.providers.supplement.mechanism import SIGNOR_COLUMNS
from bioagent.runtime.component import ComponentManifest, Permissions, RuntimeSpec
from bioagent.status import ExecutionStatus

pytestmark = pytest.mark.unit

#: The audit's first excerpt, completed as PHP prints an uncaught error with html_errors on.
PHP_FATAL = (b"<br />\n<b>Fatal error</b>:  Uncaught TypeError: array_map(): Argument #2 ($array) "
             b"must be of type array, string given in /var/www/html/getData.php:58\n"
             b"Stack trace:\n#0 /var/www/html/getData.php(58): array_map(Object(Closure), '')\n"
             b"#1 {main}\n  thrown in <b>/var/www/html/getData.php</b> on line <b>58</b><br />\n")

#: The four accessions of the registered example: EPHA2, PTPN11, RAF1, BRAF.
CONNECT = ("P29317", "Q06124", "P04049", "P15056")


def signor_row(a, ida, b, idb, effect, mechanism, sid, *, ta="protein", tb="protein",
               dba="UNIPROT", dbb="UNIPROT", width=len(SIGNOR_COLUMNS)) -> str:
    fields = dict.fromkeys(SIGNOR_COLUMNS, "")
    fields.update(ENTITYA=a, TYPEA=ta, IDA=ida, DATABASEA=dba, ENTITYB=b, TYPEB=tb, IDB=idb,
                  DATABASEB=dbb, EFFECT=effect, MECHANISM=mechanism, TAX_ID="9606",
                  PMID="10000001", DIRECT="t", ANNOTATOR="fixture", SIGNOR_ID=sid,
                  SCORE="0.8")
    return "\t".join(list(fields.values())[:width])


#: A positive control in the shape of the connect answer: relations among the four proteins.
POSITIVE = "\n".join([
    signor_row("BRAF", "P15056", "RAF1", "P04049", "up-regulates activity", "binding",
               "SIGNOR-900001"),
    signor_row("PTPN11", "Q06124", "RAF1", "P04049", "up-regulates activity",
               "dephosphorylation", "SIGNOR-900002"),
    signor_row("EPHA2", "P29317", "PTPN11", "Q06124", "up-regulates activity",
               "phosphorylation", "SIGNOR-900003"),
    signor_row("RAF1", "P04049", "BRAF", "P15056", "up-regulates activity", "binding",
               "SIGNOR-900004"),
]) + "\n"


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[bytes, str]] = {}

    def do_GET(self):  # noqa: N802 - http.server API
        path = self.path.split("?", 1)[0]
        body, ctype = self.routes.get(path, (b"not here", "text/plain"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _Handler.routes = {
        "/php": (PHP_FATAL, "text/plain;charset=UTF-8"),
        "/positive": (POSITIVE.encode(), "text/plain;charset=UTF-8"),
        "/empty": (b"", "text/plain;charset=UTF-8"),
        "/challenge": (b"<!DOCTYPE html><html><body>Checking your browser before accessing"
                       b"</body></html>", "text/html"),
        # a field lost from the second row shifts every field after it by one
        "/shifted": ((signor_row("BRAF", "P15056", "RAF1", "P04049", "up-regulates activity",
                                 "binding", "SIGNOR-900001") + "\n"
                      + signor_row("RAF1", "P04049", "BRAF", "P15056", "up-regulates",
                                   "binding", "SIGNOR-900004").replace("\tprotein\t", "\t", 1)
                      + "\n").encode(), "text/plain"),
        # a row without its second endpoint
        "/one_endpoint": ((signor_row("BRAF", "P15056", "", "", "up-regulates activity",
                                      "binding", "SIGNOR-900001", tb="") + "\n").encode(),
                          "text/plain"),
        # data, then the PHP memory error a server prints when it dies part-way through
        "/died": ((POSITIVE * 400) .encode() + b"\n<br />\n<b>Fatal error</b>:  Allowed memory "
                  b"size of 134217728 bytes exhausted (tried to allocate 20480 bytes) in "
                  b"/var/www/html/getData.php on line 40<br />\n", "text/plain"),
    }
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _signor(server: str) -> ComponentManifest:
    return ComponentManifest(id="t.signor", kind="connector", name="signor",
                             runtime=RuntimeSpec(backend="http", server=server),
                             permissions=Permissions(network=("127.0.0.1",)))


def _call(server: str, path: str, tmp_path=None):
    call = render_call("signor", "relations_among", proteins=",".join(CONNECT))
    call["path"] = path
    return HTTPBackend(rates={"127.0.0.1": 1000.0}, max_retries=1,
                       cache_dir=tmp_path).invoke(_signor(server), **call)


# ---------------------------------------------------------------- error reports

@pytest.mark.parametrize("body", [
    PHP_FATAL,
    b"PHP Fatal error:  Allowed memory size of 134217728 bytes exhausted in /srv/a.php on line 3",
    b"<b>Warning</b>:  mysqli_connect(): (HY000/2002): Connection refused in "
    b"<b>/var/www/db.php</b> on line <b>9</b><br />",
    b"Traceback (most recent call last):\n  File \"/srv/app.py\", line 3, in <module>\n"
    b"    raise ValueError('x')\nValueError: x\n",
    b"java.lang.NullPointerException: null\n\tat org.example.Search.run(Search.java:42)\n",
    b"<html><head><title>Runtime Error</title></head><body><h1>Server Error in '/' "
    b"Application.</h1></body></html>",
])
def test_an_error_report_is_recognised(body):
    assert _error_report(body), body[:60]


@pytest.mark.parametrize("body", [
    POSITIVE.encode(),
    b'{"error": "Traceback (most recent call last):\\n  File \\"x\\""}',   # the API's own JSON
    b'<?xml version="1.0"?><PubmedArticleSet><ArticleTitle>A fatal error in dosing'
    b'</ArticleTitle></PubmedArticleSet>',
    b"ID\tName\nCHEBI:1\tBerberine\n",
    b"",
])
def test_data_is_not_mistaken_for_an_error_report(body):
    assert _error_report(body) is None


def test_the_audits_signor_answer_fails_and_is_not_cached(server, tmp_path):
    """The reproduced P0: HTTP 200, text/plain, a PHP fatal error -> never SUCCEEDED."""
    res = _call(server, "php", tmp_path)
    assert res.status is ExecutionStatus.FAILED and res.value is None
    assert "error report" in res.error and "PHP" in res.error
    check = res.metadata["content_check"]
    assert check == {**check, "ok": False, "check": "server_error_report",
                     "layer": "upstream_content"}
    assert res.metadata["failure_layer"] == "upstream_content"
    assert not list(tmp_path.glob("*.json.gz")), "an error report is never cached"


def test_an_error_printed_after_partial_data_fails_the_whole_answer(server):
    res = _call(server, "died")
    assert res.status is ExecutionStatus.FAILED
    assert "Allowed memory" in res.error


def test_a_challenge_page_is_not_data(server):
    res = _call(server, "challenge")
    assert res.status is ExecutionStatus.UNAVAILABLE and res.value is None
    assert res.metadata["content_check"]["check"] == "html_page"


def test_a_cached_error_report_is_not_served(server, tmp_path):
    """An entry cached before error reports were refused must not come back as a result."""
    backend = HTTPBackend(cache_dir=tmp_path, rates={"127.0.0.1": 1000.0}, max_retries=1)
    req = HTTPRequest(url=server + "/php", accept="text/plain")
    with gzip.open(tmp_path / f"{req.key()}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({"http_status": 200, "content_type": "text/plain",
                   "value": {"format": "xml", "text": PHP_FATAL.decode()}}, fh)
    status, value, _, meta = backend.request(req)
    assert status is ExecutionStatus.FAILED and not meta["cached"]


# ------------------------------------------------------------------ the contract

def test_both_signor_relation_operations_declare_their_table():
    for op in ("entity_relations", "relations_among"):
        call = render_call("signor", op, **BY_KEY["signor"].op(op).example)
        contract = call["contract"]
        assert contract["columns"] == list(SIGNOR_COLUMNS) and len(SIGNOR_COLUMNS) == 28
        assert {"ENTITYA", "IDA", "ENTITYB", "IDB", "EFFECT", "SIGNOR_ID"} <= set(
            contract["required"])
        assert contract["min_rows"] == 1


def test_the_four_accession_positive_control_returns_relation_rows(server, tmp_path):
    res = _call(server, "positive", tmp_path)
    assert res.status is ExecutionStatus.SUCCEEDED, res.error
    rows = res.value["rows"]
    assert len(rows) == 4 and res.value["columns"] == list(SIGNOR_COLUMNS)
    # keyed by SIGNOR's columns, not by the first relation's values
    assert rows[0]["ENTITYA"] == "BRAF" and rows[0]["IDB"] == "P04049"
    assert all(r["IDA"] and r["IDB"] and r["EFFECT"] for r in rows)
    assert {r["IDA"] for r in rows} | {r["IDB"] for r in rows} == set(CONNECT)
    assert res.metadata["content_check"] == {"ok": True, "check": "response_contract",
                                             "rows": 4}
    # cached under its contract, and served again from the cache as the same rows
    again = HTTPBackend(rates={"127.0.0.1": 1000.0}, cache_dir=tmp_path).invoke(
        _signor(server), **{**render_call("signor", "relations_among",
                                          proteins=",".join(CONNECT)), "path": "positive"})
    assert again.metadata["cached"] and again.value["rows"] == rows


@pytest.mark.parametrize("path, words", [
    ("empty", "0 row(s)"),
    ("shifted", "malformed"),
    ("one_endpoint", "no ENTITYB, TYPEB, IDB"),
])
def test_an_answer_that_does_not_fit_fails(server, path, words):
    res = _call(server, path)
    assert res.status is ExecutionStatus.FAILED and res.value is None
    assert words in res.error, res.error
    assert res.metadata["content_check"]["check"] == "response_contract"


def test_a_contract_names_only_its_columns_and_its_sample_fits():
    with pytest.raises(ValueError):
        table_contract(("A", "B"), required=("C",))
    contract = table_contract(("A", "B", "ID"), required=("A", "ID"),
                              patterns={"ID": r"X-\d+(?#e.g. X-1)"})
    assert contract["min_fields"] == 3
    parsed = HTTPBackend._parse(contract_sample(contract), "text/plain", contract=contract)
    assert _contract_problems(parsed, contract) == []
    assert parsed["rows"][0]["ID"] == "X-1"
    signor = BY_KEY["signor"].op("relations_among").contract
    assert _contract_problems(HTTPBackend._parse(contract_sample(signor), "text/plain",
                                                 contract=signor), signor) == []
