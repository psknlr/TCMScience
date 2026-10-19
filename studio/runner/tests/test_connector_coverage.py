"""studio/docs/compat/connectors.{csv,md}: every connector operation by verification level, generated and kept current
(studio/scripts/connector_coverage.py). A mock result is never counted as a live one."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "connector_coverage.py"
sys.path.insert(0, str(SCRIPT.parent))
import connector_coverage as cc  # noqa: E402


@pytest.fixture(scope="module")
def doc():
    return cc.catalog()


@pytest.fixture(scope="module")
def connectors(doc):
    return sorted((e for e in doc["entries"] if e["kind"] == "connector"), key=lambda e: e["id"])


def test_the_committed_report_is_current_and_no_operation_fails_offline():
    res = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr


def test_one_row_per_connector_entry_and_live_comes_only_from_the_committed_record(doc, connectors):
    table = cc.rows(doc, checked={})
    assert [r["id"] for r in table] == [e["id"] for e in connectors]
    for r, e in zip(table, connectors):
        level = (e["verification"]["levels"] or {}).get("live_upstream")
        assert r["live_upstream"] == (level["status"] if level else "never"), r["id"]
        assert r["live_upstream_at"] == ((level or {}).get("at") or ""), r["id"]
        assert r["live_gateway"] == cc.NOT_VERIFIED
        assert r["mock_transport"] == "UNVERIFIED"          # nothing was checked: never a pass


def test_a_missing_live_row_is_never_and_a_mock_pass_does_not_make_it_live(doc, connectors):
    live = cc.live_rows()
    first = connectors[0]["connector"]
    planted = {k: v for k, v in live.items() if k != (first["key"], first["operation"])}
    table = cc.rows(doc, live=planted, scheduled=[],
                    checked={e["id"]: {"mock_transport": "PASS", "mock_detail": "", "offline_refusal": "PASS"}
                             for e in connectors})
    by = {r["id"]: r for r in table}
    gone = by[connectors[0]["id"]]
    assert gone["mock_transport"] == "PASS" and gone["live_upstream"] == "never" and gone["live_upstream_at"] == ""
    assert by[connectors[1]["id"]]["live_upstream"] == "PASS"
    text = cc.summary(table, scheduled=[])
    assert "never 1" in text and "not verified" in text and "no workflow schedules" in text
    # with no record at all, nothing is live, however many mocks passed
    none = cc.rows(doc, live={}, scheduled=[], checked={e["id"]: {"mock_transport": "PASS"} for e in connectors})
    assert {r["live_upstream"] for r in none} == {"never"}


def test_the_mock_check_fails_an_operation_whose_request_or_answer_is_wrong(connectors, monkeypatch):
    some = [e for e in connectors if e["id"] in ("connector.uniprot.entry", "connector.gprofiler.gost")]
    assert len(some) == 2
    good = cc.check_operations(some)
    assert {r["mock_transport"] for r in good.values()} == {"PASS"}
    assert {r["offline_refusal"] for r in good.values()} == {"PASS"}
    # an upstream error through the transport comes back failed, so it is not a pass
    bad = cc.check_operations(some, answer=lambda p: {"status": 503, "headers": {}, "body_base64": "", "url": p["url"]})
    assert {r["mock_transport"] for r in bad.values()} == {"FAIL"}
    assert all("the envelope is failed" in r["mock_detail"] for r in bad.values())
    # a request that is not the one verify_connectors sends is a failure
    from bioagent.providers import public_apis
    real = public_apis.render_call
    monkeypatch.setattr(public_apis, "render_call", lambda key, op, **kw: {**real(key, op, **kw), "path": "elsewhere"})
    drift = cc.check_operations(some)
    assert {r["mock_transport"] for r in drift.values()} == {"FAIL"}
    assert all("not the request verify_connectors sends" in r["mock_detail"] for r in drift.values())


def test_the_transport_stand_in_is_removed_afterwards():
    from bioagent.backends import http as H
    before = (H.HTTPBackend.browser_opener_factory, H._RateLimiter.wait)
    with cc.mock_transport():
        assert H.HTTPBackend.browser_opener_factory is not before[0]
    assert (H.HTTPBackend.browser_opener_factory, H._RateLimiter.wait) == before


def test_a_schedule_is_read_from_a_workflow_that_runs_the_live_verification(tmp_path):
    (tmp_path / "live.yml").write_text('on:\n  schedule:\n    - cron: "17 4 3 * *"\n  workflow_dispatch:\n'
                                       "jobs:\n  x:\n    steps:\n      - run: python scripts/verify_connectors.py\n")
    (tmp_path / "manual.yml").write_text("on:\n  workflow_dispatch:\njobs:\n  x:\n    steps:\n"
                                         "      - run: python scripts/verify_connectors.py --no-write\n")
    (tmp_path / "other.yml").write_text('on:\n  schedule:\n    - cron: "0 0 * * 0"\n')
    found = cc.schedules(tmp_path)
    assert [s["cron"] for s in found] == ["17 4 3 * *"] and found[0]["file"].endswith("live.yml")


def test_the_report_carries_no_clock(doc):
    text = (cc.OUT / "connectors.md").read_text(encoding="utf-8")
    dates = {r["live_upstream_at"][:10] for r in cc.rows(doc, checked={})} - {""}
    import re
    assert set(re.findall(r"\b20\d\d-\d\d-\d\d\b", text)) <= dates
