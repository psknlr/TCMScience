"""Reading the published corpus (docs/V2.md §11.4): verified reads, the read log, the LRU,
the three fetchers (directory, HTTP with a cache and the network switch, the browser's
synchronous XHR), and how a call finds its corpus."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys

import pytest

from tcmstudio.corpus import reader as R
from tcmstudio.corpus.reader import (Corpus, CorpusError, DirFetcher, HttpFetcher, XHRFetcher,
                                     corpus_home, default_corpus, reset_default_corpora)
from test_corpus_support import FakeJS, boot_entry, serve_directory, tiny_corpus

pytest.importorskip("openpyxl")


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    return tiny_corpus(tmp_path_factory)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("TCMSTUDIO_CORPUS", raising=False)
    reset_default_corpora()
    yield
    reset_default_corpora()


def test_dir_fetcher_reads_a_site_or_its_corpus_directory(tiny):
    for root in (tiny["site"], tiny["site"] / "corpus"):
        c = Corpus(DirFetcher(root))
        assert c.snapshot_id == tiny["summary"]["snapshot_id"]
        assert c.manifest_sha256 == tiny["summary"]["sha256"]
        core = c.get("core/core.json")
        assert len(core["herbs"]) == 401


def test_reads_are_ordered_deduplicated_and_per_session(tiny):
    c = Corpus(DirFetcher(tiny["site"]))
    s1, s2 = c.session(), c.session()
    s1.get("formulas/index.json")
    s1.get("core/core.json")
    s1.get("formulas/index.json")
    s2.get("core/core.json")
    assert [r["path"] for r in s1.reads()] == ["formulas/index.json", "core/core.json"]
    assert [r["path"] for r in s2.reads()] == ["core/core.json"]
    assert s1.reads()[0]["sha256"] == c.manifest["objects"]["formulas/index.json"]["sha256"]
    assert s1.packs_read == ["formulas", "core"]
    receipt = s1.receipt()
    assert receipt["snapshot_id"] == c.snapshot_id and receipt["reads_total"] == 2
    assert receipt["manifest_sha256"] == c.manifest_sha256


def test_parsed_objects_are_cached_and_evicted(tiny):
    c = Corpus(DirFetcher(tiny["site"]), cache_bytes=10)          # holds one object at most
    a = c.get("formulas/stats.json")
    assert c.get("formulas/stats.json") is a                        # still the last one
    c.get("formulas/index.json")
    assert c.get("formulas/stats.json") is not a                    # evicted, parsed again
    big = Corpus(DirFetcher(tiny["site"]))
    x = big.get("formulas/stats.json")
    big.get("formulas/index.json")
    assert big.get("formulas/stats.json") is x


def test_bytes_that_do_not_match_the_manifest_are_never_used(tiny, tmp_path):
    site = tmp_path / "site"
    shutil.copytree(tiny["site"], site)
    c = Corpus(DirFetcher(site))
    url = c.manifest["objects"]["formulas/stats.json"]["url"]
    (site / "corpus" / url).write_bytes(b"\x1f\x8b tampered")
    with pytest.raises(CorpusError) as err:
        c.get("formulas/stats.json")
    assert err.value.type == "unavailable" and "does not match" in str(err.value)
    with pytest.raises(CorpusError) as err:
        Corpus(DirFetcher(site), manifest=tiny["summary"]["manifest"], sha256="0" * 64)
    assert "pinned" in str(err.value)
    with pytest.raises(CorpusError) as err:
        c.get("formulas/nothing.json")
    assert err.value.type == "not_in_snapshot"


def test_a_missing_corpus_is_unavailable_with_a_remedy(tmp_path):
    with pytest.raises(CorpusError) as err:
        Corpus(DirFetcher(tmp_path / "nowhere"))
    assert err.value.type == "unavailable" and err.value.hint


def test_http_fetcher_caches_and_honours_the_network_switch(tiny, tmp_path):
    cache = tmp_path / "cache"
    with serve_directory(tiny["site"]) as base:
        online = Corpus(HttpFetcher(base + "corpus/", cache, network=True))
        assert online.snapshot_id == tiny["summary"]["snapshot_id"]
        online.get("core/core.json")
        missing = HttpFetcher(base + "nothing/", tmp_path / "c2", network=True)
        with pytest.raises(CorpusError) as err:
            missing.read("latest.json")
        assert err.value.type == "unavailable" and "HTTP 404" in str(err.value)
    # the server is gone: what was cached is still read (and verified), the rest is network_off
    offline = Corpus(HttpFetcher("http://127.0.0.1:9/corpus/", cache, network=False))
    assert offline.get("core/core.json")["herbs"]
    with pytest.raises(CorpusError) as err:
        offline.get("formulas/index.json")
    assert err.value.type == "network_off" and "tcmstudio corpus fetch" in err.value.hint
    empty = HttpFetcher("http://127.0.0.1:9/corpus/", tmp_path / "empty", network=False)
    with pytest.raises(CorpusError) as err:
        Corpus(empty)
    assert err.value.type == "network_off"


def test_xhr_fetcher_reads_through_a_synchronous_request(tiny, monkeypatch):
    base = "https://science.test/corpus/"
    fake = FakeJS(tiny["site"] / "corpus", base)
    monkeypatch.setitem(sys.modules, "js", fake)
    entry = boot_entry(tiny["summary"])
    c = Corpus(XHRFetcher(base), manifest=entry["manifest"], sha256=entry["sha256"])
    d = Corpus(DirFetcher(tiny["site"]))
    assert c.get("core/core.json") == d.get("core/core.json")
    assert fake.requests == [base + tiny["summary"]["manifest"],
                             base + c.manifest["objects"]["core/core.json"]["url"]]
    with pytest.raises(CorpusError) as err:
        XHRFetcher("https://science.test/nothing/").read("latest.json")
    assert "HTTP 404" in str(err.value)


def test_xhr_fetcher_outside_the_browser_says_so(monkeypatch):
    monkeypatch.delitem(sys.modules, "js", raising=False)
    with pytest.raises(CorpusError) as err:
        XHRFetcher("https://science.test/corpus/").read("latest.json")
    assert err.value.type == "unavailable"


def test_default_corpus_follows_the_context_then_the_environment(tiny, tmp_path, monkeypatch):
    site = tiny["site"]
    assert default_corpus({"corpus": {"dir": str(site)}}).snapshot_id == tiny["summary"]["snapshot_id"]
    # the browser worker passes the boot entry
    base = "https://science.test/corpus/"
    monkeypatch.setitem(sys.modules, "js", FakeJS(site / "corpus", base))
    entry = boot_entry(tiny["summary"])
    c = default_corpus({"where": "browser", "corpus": {"base_url": base, **entry}})
    assert isinstance(c.fetcher, XHRFetcher) and c.manifest_sha256 == entry["sha256"]
    assert default_corpus({"where": "browser", "corpus": {"base_url": base, **entry}}) is c
    with pytest.raises(CorpusError, match="no corpus"):
        default_corpus({"where": "browser"})
    # $TCMSTUDIO_CORPUS: a directory, or a URL
    monkeypatch.setenv("TCMSTUDIO_CORPUS", str(site))
    assert isinstance(default_corpus({"where": "runner"}).fetcher, DirFetcher)
    reset_default_corpora()
    with serve_directory(site) as url:
        monkeypatch.setenv("TCMSTUDIO_CORPUS", url + "corpus/latest.json")
        c = default_corpus({"where": "runner", "network": True,
                            "corpus": {"cache_dir": str(tmp_path / "cache")}})
        assert isinstance(c.fetcher, HttpFetcher) and c.snapshot_id == tiny["summary"]["snapshot_id"]
    monkeypatch.delenv("TCMSTUDIO_CORPUS")
    reset_default_corpora()
    # the runner's home cache, network off and nothing cached: network_off with the remedy
    monkeypatch.setattr(R, "_site_candidates", lambda: [])
    with pytest.raises(CorpusError) as err:
        default_corpus({"where": "runner", "network": False,
                        "state_root": str(tmp_path / "home" / "projects")})
    assert err.value.type == "network_off"


def test_corpus_home():
    assert str(corpus_home({"state_root": "/x/home/projects"})) == "/x/home/corpus"
    assert str(corpus_home({"corpus": {"cache_dir": "/c"}})) == "/c"
    assert corpus_home({}).name == "corpus"


def test_a_snapshot_in_another_format_is_refused(tiny, tmp_path):
    site = tmp_path / "site"
    shutil.copytree(tiny["site"], site)
    m = site / "corpus" / tiny["summary"]["manifest"]
    doc = json.loads(m.read_text(encoding="utf-8"))
    doc["schema"] = "tcmstudio.corpus/9"
    data = json.dumps(doc).encode()
    name = f"manifest.{hashlib.sha256(data).hexdigest()[:12]}.json"
    (site / "corpus" / name).write_bytes(data)
    (site / "corpus" / "latest.json").write_text(json.dumps(
        {"manifest": name, "sha256": hashlib.sha256(data).hexdigest()}), encoding="utf-8")
    with pytest.raises(CorpusError, match="newer format|schema"):
        Corpus(DirFetcher(site))
