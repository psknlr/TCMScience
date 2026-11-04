"""The UniProt accession list assembled from pages, and the snapshot pipeline's use of it.

Audit of 2026-11-04 (P1): the bulk ``stream`` answered HTTP 503 twice and stopped the
standard snapshot build at its acquisition step, while the same query paged through
``search`` gave the complete list (20,431 accessions, 41 pages, release 2026_03, P04637
among them). These tests hold the fallback to the checks the audit asked for — one
release, complete against the service's own total, duplicates counted, every line an
accession, known accessions present, the file's digest recorded and held — and the
pipeline's report of what a missing mapping file blocks. A fake UniProt serves the pages;
nothing here touches the network.
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

from bioagent.acquisition import uniprot as U
from bioagent.acquisition.sources import ACQUIRABLE

pytestmark = pytest.mark.unit

STREAM = ("https://rest.uniprot.org/uniprotkb/stream?query=organism_id:9606+AND+reviewed:true"
          "&format=list")


def _accessions(n: int) -> list[str]:
    """``n`` well-formed Swiss-Prot style accessions, P04637 first."""
    out = ["P04637"]
    i = 0
    while len(out) < n:
        out.append(f"Q{i // 10000 % 10}{chr(65 + i // 1000 % 26)}{i // 100 % 10}{i // 10 % 10}{i % 10}")
        i += 1
    return out


class FakeUniProt:
    """Pages of ``accessions`` by cursor, with UniProt's headers; ``hooks`` alter one page."""

    def __init__(self, accessions, *, size=500, release="2026_03", total=None, fail_first=0):
        self.accs, self.size, self.release = list(accessions), size, release
        self.total = len(set(accessions)) if total is None else total
        self.fail_first = fail_first
        self.calls: list[str] = []
        self.hooks: dict[int, dict] = {}

    def __call__(self, url, timeout):
        self.calls.append(url)
        if self.fail_first:
            self.fail_first -= 1
            return 503, {"Retry-After": "1"}, b"Service Unavailable"
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert urllib.parse.urlsplit(url).path == "/uniprotkb/search"
        assert q["format"] == "list" and q["query"] == "organism_id:9606 AND reviewed:true"
        page = int(q.get("cursor", "0"))
        chunk = self.accs[page * self.size:(page + 1) * self.size]
        headers = {"X-Total-Results": str(self.total), "X-UniProt-Release": self.release,
                   "X-UniProt-Release-Date": "01-October-2026"}
        if (page + 1) * self.size < len(self.accs):
            nxt = urllib.parse.urlencode({**q, "cursor": str(page + 1)})
            headers["Link"] = f'<https://rest.uniprot.org/uniprotkb/search?{nxt}>; rel="next"'
        body = "\n".join(chunk) + "\n"
        hook = self.hooks.get(page) or {}
        headers.update(hook.get("headers") or {})
        body = hook.get("body", body)
        return 200, headers, body.encode()


def _run(tmp_path, fake, **kw):
    return U.paginated_accessions(STREAM, tmp_path, "uniprot_human_reviewed_accessions.txt",
                                  fetch=fake, sleep=lambda s: None, log=lambda m: None,
                                  min_interval_s=0, **kw)


def test_the_search_url_is_the_stream_query_paged():
    url = U.search_url(STREAM)
    parts = urllib.parse.urlsplit(url)
    assert parts.netloc == "rest.uniprot.org" and parts.path == "/uniprotkb/search"
    assert dict(urllib.parse.parse_qsl(parts.query)) == {
        "query": "organism_id:9606 AND reviewed:true", "format": "list", "size": "500"}
    with pytest.raises(U.PaginationError):
        U.search_url("https://evil.example/uniprotkb/stream?query=x")


def test_a_complete_list_is_assembled_from_its_pages_and_recorded(tmp_path):
    accs = _accessions(1203)
    fake = FakeUniProt(accs)
    r = _run(tmp_path, fake)
    assert (r.pages, r.unique, r.total, r.duplicates, r.release) == (3, 1203, 1203, 0, "2026_03")
    text = (tmp_path / "uniprot_human_reviewed_accessions.txt").read_text()
    assert text.splitlines() == accs, "the upstream's order, one accession per line"
    side = U.read_sidecar(tmp_path / "uniprot_human_reviewed_accessions.txt")
    assert side["release"] == "2026_03" and side["checksum"] == f"sha256:{r.sha256}"
    assert all(c["status"] in ("PASS", "NOT_APPLICABLE") for c in side["checks"].values())
    manifest = json.loads((tmp_path / ".downloads.json").read_text())
    assert manifest["uniprot_human_reviewed_accessions.txt"]["route"] == "paginated"
    assert U.check_sidecar(tmp_path / "uniprot_human_reviewed_accessions.txt")[0] == "PASS"


def test_a_duplicate_across_pages_is_counted_and_kept_once(tmp_path):
    accs = _accessions(1000)
    paged = accs[:500] + [accs[499]] + accs[500:]          # page 2 repeats page 1's last
    r = _run(tmp_path, FakeUniProt(paged, total=1000))
    assert r.unique == 1000 and r.duplicates == 1
    assert r.checks["duplicates"]["count"] == 1


def test_a_duplicate_never_makes_up_for_a_missing_accession(tmp_path):
    accs = _accessions(1000)
    paged = accs[:500] + [accs[499]] + accs[500:999]       # one repeated, one lost
    with pytest.raises(U.PaginationError) as exc:
        _run(tmp_path, FakeUniProt(paged, total=1000))
    assert exc.value.check == "completeness" and "999 unique" in str(exc.value)
    assert not (tmp_path / "uniprot_human_reviewed_accessions.txt").exists(), "nothing written"


def test_a_release_that_changes_while_paging_fails(tmp_path):
    fake = FakeUniProt(_accessions(1203))
    fake.hooks[2] = {"headers": {"X-UniProt-Release": "2026_04"}}
    with pytest.raises(U.PaginationError) as exc:
        _run(tmp_path, fake)
    assert exc.value.check == "release" and "2026_04" in str(exc.value)


def test_an_error_page_among_the_pages_fails(tmp_path):
    fake = FakeUniProt(_accessions(1203))
    fake.hooks[1] = {"body": "<html><body>Service temporarily unavailable</body></html>\n"}
    with pytest.raises(U.PaginationError) as exc:
        _run(tmp_path, fake)
    assert exc.value.check == "format"


def test_a_list_without_its_known_accessions_fails(tmp_path):
    accs = _accessions(800)[1:]                                 # P04637 left out
    with pytest.raises(U.PaginationError) as exc:
        _run(tmp_path, FakeUniProt(accs))
    assert exc.value.check == "expected" and "P04637" in str(exc.value)


def test_a_next_link_off_uniprot_is_refused(tmp_path):
    fake = FakeUniProt(_accessions(1203))
    fake.hooks[0] = {"headers": {"Link": '<https://evil.example/uniprotkb/search?cursor=1>; '
                                         'rel="next"'}}
    with pytest.raises(U.PaginationError) as exc:
        _run(tmp_path, fake)
    assert exc.value.check == "host"


def test_a_503_is_retried_with_the_pause_asked_for(tmp_path):
    slept = []
    fake = FakeUniProt(_accessions(600), fail_first=2)
    r = U.paginated_accessions(STREAM, tmp_path, "u.txt", fetch=fake, sleep=slept.append,
                               log=lambda m: None, min_interval_s=0)
    assert r.unique == 600 and len(fake.calls) == 4
    assert all(s >= 1 for s in slept[:2])


def test_the_same_release_assembled_again_must_give_the_same_digest(tmp_path):
    accs = _accessions(900)
    first = _run(tmp_path, FakeUniProt(accs))
    again = _run(tmp_path, FakeUniProt(accs))
    assert again.sha256 == first.sha256
    assert again.checks["same_release"]["status"] == "PASS"
    drifted = accs[:-1] + ["A0A024RBG1"]
    with pytest.raises(U.PaginationError) as exc:
        _run(tmp_path, FakeUniProt(drifted))
    assert exc.value.check == "same_release"
    # a new release is a new file, not drift
    assert _run(tmp_path, FakeUniProt(drifted, release="2026_04")).release == "2026_04"


def test_a_file_changed_after_assembly_fails_its_integrity_check(tmp_path):
    _run(tmp_path, FakeUniProt(_accessions(700)))
    path = tmp_path / "uniprot_human_reviewed_accessions.txt"
    path.write_text(path.read_text() + "P99999\n")
    status, why = U.check_sidecar(path)
    assert status == "FAIL" and "changed since" in why


# ------------------------------------------------------------------ the pipeline

def _spec():
    return next(a for a in ACQUIRABLE if a.filename == "uniprot_human_reviewed_accessions.txt")


def test_the_uniprot_spec_names_its_fallback():
    assert _spec().fallback == "uniprot_paginated"


def test_the_pipeline_pages_the_query_when_the_stream_fails(tmp_path, monkeypatch):
    """The audit's case: the stream answers 503; the build is not stopped by it."""
    from bioagent.acquisition import transfer
    from bioagent.acquisition.downloader import DownloadError
    from bioagent.sources import pipeline

    def stream_503(self, url, name, **kw):
        raise DownloadError(f"{url}: HTTP 503 Service Unavailable")
    monkeypatch.setattr(transfer.VerifiedTransfer, "fetch", stream_503)
    fake = FakeUniProt(_accessions(1203))
    monkeypatch.setattr(U, "_default_fetch", fake)
    monkeypatch.setattr(U.time, "sleep", lambda s: None)
    rows = pipeline._acquire(tmp_path, [_spec()], confirm=False, offline=False,
                             log=lambda m: None)
    row = rows[0]
    assert row["status"] == "PASS" and row["route"] == "paginated", row
    assert "503" in row["primary"]["reason"], "the stream's failure is kept"
    assert (row["release"], row["unique"], row["pages"]) == ("2026_03", 1203, 3)
    checks = pipeline._integrity(tmp_path, [_spec()])
    assert checks[0]["status"] == "UNVERIFIED", "no reviewed digest of its own"
    assert "assembled from 3 pages of UniProt release 2026_03" in checks[0]["reason"]
    # what plan() says of it now
    doc = pipeline.plan(tmp_path, ["npass"])
    uni = next(f for f in doc["files"] if f["file"] == _spec().filename)
    assert uni["assembled"]["release"] == "2026_03" and uni["assembled"]["pages"] == 3


def test_a_policy_refusal_is_not_routed_around(tmp_path, monkeypatch):
    from bioagent.acquisition import transfer
    from bioagent.acquisition.downloader import DownloadError
    from bioagent.sources import pipeline

    def denied(self, url, name, **kw):
        raise DownloadError("network access denied for rest.uniprot.org by the network policy")
    monkeypatch.setattr(transfer.VerifiedTransfer, "fetch", denied)
    fake = FakeUniProt(_accessions(10))
    monkeypatch.setattr(U, "_default_fetch", fake)
    rows = pipeline._acquire(tmp_path, [_spec()], confirm=False, offline=False,
                             log=lambda m: None)
    assert rows[0]["status"] == "BLOCKED" and not fake.calls


def test_before_building_a_missing_mapping_file_names_the_analyses_it_blocks(tmp_path):
    from bioagent.sources import pipeline
    doc = pipeline.plan(tmp_path, ["npass"])
    assert "uniprot_human_reviewed_accessions.txt" in doc["blocked"]
    assert "PubChem BioAssay targets" in doc["blocked"]["uniprot_human_reviewed_accessions.txt"]
    assert "Reactome pathway enrichment" in doc["blocked"]["UniProt2Reactome.txt"]
    report = pipeline.build_research_snapshots(tmp_path / "raw", tmp_path / "snap",
                                               tmp_path / "ledger.jsonl", sources=["npass"],
                                               offline=True, log=lambda m: None)
    discovery = report["stages"][0]
    assert discovery["stage"] == "discovery"
    assert "uniprot_human_reviewed_accessions.txt" in discovery["blocked"]
    assert "cannot run" in discovery["detail"]
