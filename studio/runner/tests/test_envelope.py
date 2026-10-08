"""The envelope (CONTRACTS §3): invariants that hold whatever goes in."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import types

import pytest

from tcmstudio import envelope as env
from tcmstudio.envelope import (canonical_json, extract_citations, fit_json, jsonable, shape,
                                sha256_json)

KEYS = {"ok", "tool", "via", "status", "duration_ms", "summary", "summary_en", "text", "result",
        "citations", "governance", "receipt", "job", "approval", "error"}


class Unrenderable:
    def __repr__(self):
        raise RuntimeError("cannot repr")


def cyclic():
    a: dict = {"name": "a"}
    a["self"] = a
    return a


def deep(n):
    value = "deep"
    for _ in range(n):
        value = [value]
    return value


GARBAGE = [None, 0, -1.5, float("nan"), float("inf"), "文本", b"\x00\xff", {1, 2, 3},
           frozenset({"x"}), (1, (2, (3,))), cyclic(), Unrenderable(), {"k": Unrenderable()},
           deep(200), {"nested": {"set": {1}, "bytes": b"ab", "nan": math.nan}}, object(), type,
           sys]


@pytest.mark.parametrize("value", GARBAGE, ids=lambda v: type(v).__name__)
def test_shape_never_raises_and_is_strict_json(value):
    e = shape("tool", via="native.x", kind="native", result=value, arguments={"v": value},
              governance={"limitations": value, "claims": value},
              citations=[{"kind": "pmid", "evidence_ref": "pmid:1"}, value],
              error={"type": "nonsense", "message": value} if value is not None else None)
    assert set(e) == KEYS
    json.dumps(e, ensure_ascii=False, allow_nan=False)
    assert len(e["text"]) <= 16_000
    if e["error"]:
        assert e["error"]["type"] in env.ERROR_TYPES


def test_unknown_status_and_kind_are_normalised():
    e = shape("t", status="exploded", kind="martian")
    assert e["status"] == "failed" and e["ok"] is False
    assert e["governance"]["kind"] == "system"


def test_jsonable_handles_floats_and_enums():
    import enum

    class Tier(enum.IntEnum):
        CLASSICAL_TEXT = 1

    class Kind(enum.Enum):
        A = "attribution"

    assert jsonable({"a": math.nan, "b": -math.inf, "c": 1.5}) == {"a": "NaN", "b": "-Infinity",
                                                                    "c": 1.5}
    assert jsonable([Tier.CLASSICAL_TEXT, Kind.A]) == ["CLASSICAL_TEXT", "attribution"]
    assert jsonable(cyclic())["self"] == "<cycle>"
    assert jsonable(b"abc") == {"bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}


def test_text_is_capped_and_says_so():
    huge = {"rows": [{"id": i, "text": "x" * 500} for i in range(5000)], "note": "y" * 100_000}
    e = shape("t", result=huge, governance={"limitations": ["limit " * 50] * 30})
    assert len(e["text"]) <= 16_000
    assert "…" in e["text"]
    assert len(json.dumps(e["result"])) > 1_000_000               # the UI keeps everything
    small = shape("t", result={"a": 1}, text_limit=200)
    assert len(small["text"]) <= 200


def test_fit_json_keeps_valid_json_when_it_can():
    data = {"items": list(range(10_000)), "title": "t"}
    text = fit_json(data, 400)
    assert len(text) <= 400
    parsed = json.loads(text)
    assert parsed["title"] == "t" and "more" in parsed["items"][-1]


def test_hashes_are_canonical_and_stable():
    a = {"b": [1, 2, {"z": 1, "y": "中"}], "a": 1.5}
    b = {"a": 1.5, "b": [1, 2, {"y": "中", "z": 1}]}
    assert canonical_json(a) == canonical_json(b) == '{"a":1.5,"b":[1,2,{"y":"中","z":1}]}'
    assert sha256_json(a) == sha256_json(b) == hashlib.sha256(
        canonical_json(a).encode("utf-8")).hexdigest()
    e1 = shape("t", result={"x": [1, 2]}, arguments=a)
    e2 = shape("t", result={"x": [1, 2]}, arguments=b)
    assert e1["receipt"]["input_sha256"] == e2["receipt"]["input_sha256"] == sha256_json(a)
    assert e1["receipt"]["output_sha256"] == e2["receipt"]["output_sha256"]
    # The web client hashes the same bytes: JSON.stringify of the sorted object.
    assert sha256_json({}) == hashlib.sha256(b"{}").hexdigest()


def test_limits_are_restated_in_the_text():
    gov = {"limitations": ["Absence of a record is not evidence of safety: X"],
           "released": False, "verdict": {"states": {"schema_valid": True,
                                                     "release_authorized": False},
                                          "unverified": ["no audit head"]},
           "refusals": [{"code": "CLM005", "message": "tier below floor", "remedy": "weaken"}],
           "claims": [{"id": "c1", "claim_kind": "efficacy", "text": "works"}]}
    text = shape("t", result={"r": 1}, governance=gov)["text"]
    assert "Absence of a record is not evidence of safety" in text
    assert "NOT released" in text and "states 1/2" in text and "no audit head" in text
    assert "CLM005" in text and "weaken" in text and "[efficacy] works" in text


def test_receipt_shape_and_versions():
    e = shape("t", result=1, where="browser", device="webgpu", started_at="2026-10-07T12:00:00Z")
    r = e["receipt"]
    assert r["where"] == "browser" and r["device"] == "webgpu"
    assert r["started_at"] == "2026-10-07T12:00:00Z"
    assert r["versions"]["tcmstudio"] == "0.1.0"
    assert r["versions"]["bioagent"] and r["versions"]["psh"]
    assert r["runtime"].startswith("CPython ") and " / " in r["runtime"]
    assert shape("t", where="mars")["receipt"]["where"] == "runner"


def test_runtime_string_detects_pyodide(monkeypatch):
    fake = types.ModuleType("pyodide")
    fake.__version__ = "314.0.7"
    monkeypatch.setitem(sys.modules, "pyodide", fake)
    monkeypatch.setattr(sys, "platform", "emscripten")
    assert env.runtime_string().startswith("pyodide-314.0.7 / CPython 3.")


def test_versions_fall_back_without_metadata(monkeypatch):
    import importlib.metadata as md

    def missing(name):
        raise md.PackageNotFoundError(name)
    monkeypatch.setattr(md, "version", missing)
    assert env.package_version("bioagent") == sys.modules["bioagent"].__version__
    assert env.package_version("tcmstudio") == "0.1.0"
    assert env.package_version("no_such_package_xyz") == "unknown"


def test_pyproject_fallback(tmp_path, monkeypatch):
    pkg = tmp_path / "proj" / "src" / "demo_pkg_fallback"
    pkg.mkdir(parents=True)
    (tmp_path / "proj" / "pyproject.toml").write_text(
        '[project]\nname = "demo_pkg_fallback"\nversion = "9.8.7"\n', encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "proj" / "src"))
    assert env.package_version("demo_pkg_fallback") == "9.8.7"


def test_citations_from_results_and_evidence():
    result = {"hits": [{"title": "A trial", "pmid": "12345678", "doi": "10.1000/xyz"},
                       {"title": "Another", "nctId": "NCT01234567"},
                       {"title": ["A Crossref title"], "DOI": "10.2000/only-doi"}],
              "note": "see pmid:999 and doi:10.5555/abc.def; registered as NCT07654321",
              "passages": [{"id": "passage.shl_12", "source": "伤寒论", "chapter": "第12条",
                            "text": "…"}]}
    gov = {"evidence": [{"id": "ev1", "identifier": "11111111", "identifier_type": "pmid",
                         "citation": "Doe 2020"},
                        {"id": "ev2", "identifier": "passage.x", "identifier_type": "classical_passage",
                         "citation": "《神农本草经》"}]}
    cites = extract_citations(result, gov)
    assert [c["id"] for c in cites] == [f"E{i}" for i in range(1, len(cites) + 1)]
    first = cites[0]
    assert first == {"id": "E1", "kind": "pmid", "label": "Doe 2020",
                     "url": "https://pubmed.ncbi.nlm.nih.gov/11111111/", "evidence_ref": "ev1"}
    assert cites[1]["kind"] == "classical" and cites[1]["evidence_ref"] == "ev2"
    by_ref = {c["evidence_ref"]: c for c in cites}
    assert by_ref["pmid:12345678"]["label"] == "A trial"
    assert "doi:10.1000/xyz" not in by_ref            # the same paper is cited once, by PMID
    assert by_ref["doi:10.2000/only-doi"] == {
        "id": by_ref["doi:10.2000/only-doi"]["id"], "kind": "doi", "label": "A Crossref title",
        "url": "https://doi.org/10.2000/only-doi", "evidence_ref": "doi:10.2000/only-doi"}
    assert by_ref["nct:NCT01234567"]["url"] == "https://clinicaltrials.gov/study/NCT01234567"
    assert "pmid:999" in by_ref and "doi:10.5555/abc.def" in by_ref and "nct:NCT07654321" in by_ref
    assert by_ref["passage.shl_12"]["label"] == "《伤寒论》第12条"
    assert all(c["url"].startswith("https://") or c["url"] == "" for c in cites)


def test_citations_are_deduplicated_and_capped():
    result = {"rows": [{"reference": f"pmid:{i}"} for i in range(200)] * 2}
    cites = extract_citations(result)
    assert len(cites) == 40
    assert len({c["evidence_ref"] for c in cites}) == 40
