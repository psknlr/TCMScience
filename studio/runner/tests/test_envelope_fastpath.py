"""The envelope's fast paths give exactly what the general code gives.

``jsonable`` and the citation scan take a shortcut for the plain JSON types (by exact type),
because a result is walked several times per call. The functions below are the code as it
was before the shortcut, kept verbatim: every value, however odd, must come out the same,
to the byte, and the scan must cite the same records with the same budget left.
"""
from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import decimal
import enum
import hashlib
import json
import math
import random
import types
from datetime import date, datetime  # noqa: F401 - the reference code below names them
from pathlib import PurePath, PurePosixPath
from typing import Any, Mapping

import pytest

from tcmstudio import envelope
from tcmstudio.envelope import (_MAX_CITATIONS, _MAX_DEPTH, _RE_BARE_DOI, _RE_DOI, _RE_NCT,  # noqa: F401
                                _RE_PASSAGE, _RE_PMID, _Citations, _float, _label_from,
                                identifier_url, jsonable)

# ---------------------------------------------------------------- the code before the shortcut

def ref_jsonable(value: Any, _depth: int = 0, _stack: tuple[int, ...] = ()) -> Any:
    """A JSON-compatible copy of ``value``. Never raises."""
    try:
        if value is None or isinstance(value, (bool, str)):
            return value
        if isinstance(value, enum.Enum):
            inner = value.value
            return inner if isinstance(inner, str) else value.name
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return _float(float(value))
        if _depth > _MAX_DEPTH:
            return "…"
        if id(value) in _stack:
            return "<cycle>"
        stack = _stack + (id(value),)
        if isinstance(value, Mapping):
            return {str(k) if not isinstance(k, enum.Enum) else str(ref_jsonable(k)):
                    ref_jsonable(v, _depth + 1, stack) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [ref_jsonable(v, _depth + 1, stack) for v in value]
        if isinstance(value, (set, frozenset)):
            items = [ref_jsonable(v, _depth + 1, stack) for v in value]
            try:
                return sorted(items, key=lambda x: json.dumps(x, sort_keys=True,
                                                              ensure_ascii=False))
            except Exception:                                   # noqa: BLE001
                return items
        if isinstance(value, PurePath):
            return str(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            data = bytes(value)
            return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        for method in ("document", "as_dict", "to_dict"):
            fn = getattr(value, method, None)
            if callable(fn):
                try:
                    return ref_jsonable(fn(), _depth + 1, stack)
                except Exception:                               # noqa: BLE001
                    break
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {f.name: ref_jsonable(getattr(value, f.name, None), _depth + 1, stack)
                    for f in dataclasses.fields(value)}
        tolist = getattr(value, "tolist", None)          # numpy arrays and scalars
        if callable(tolist):
            try:
                return ref_jsonable(tolist(), _depth + 1, stack)
            except Exception:                                   # noqa: BLE001
                pass
        text = repr(value)
        return text if len(text) <= 500 else text[:499] + "…"
    except Exception:                                           # noqa: BLE001
        try:
            return f"<unrenderable {type(value).__name__}>"
        except Exception:                                       # noqa: BLE001
            return "<unrenderable>"


def ref_scan(value: Any, out: _Citations, budget: list[int], depth: int = 0) -> None:
    if budget[0] <= 0 or depth > 24 or len(out.items) >= _MAX_CITATIONS:
        return
    budget[0] -= 1
    if isinstance(value, Mapping):
        rec = value
        label = _label_from(rec)
        rid = rec.get("id")
        if isinstance(rid, str) and _RE_PASSAGE.match(rid) and (rec.get("source")
                                                               or rec.get("text")):
            source = str(rec.get("source") or "").strip()
            chapter = str(rec.get("chapter") or "").strip()
            text = f"《{source}》{chapter}" if source else rid
            out.add("classical", rid, text, "", rid)
        # One citation per record: a paper with a PMID and a DOI is one paper. The PMID
        # is preferred; the record's other identifiers are marked as already cited.
        found: list[tuple[str, str, str, str]] = []
        for key in ("pmid", "PMID", "pubmed_id"):
            v = rec.get(key)
            if isinstance(v, (str, int)) and str(v).strip().isdigit():
                pid = str(v).strip()
                found.append(("pmid", pid, identifier_url("pmid", pid), f"pmid:{pid}"))
        for key in ("doi", "DOI"):
            v = rec.get(key)
            if isinstance(v, str) and v.strip().startswith("10."):
                doi = v.strip()
                found.append(("doi", doi, identifier_url("doi", doi), f"doi:{doi}"))
        for key in ("nct_id", "nctId", "NCTId", "nct"):
            v = rec.get(key)
            if isinstance(v, str) and _RE_NCT.fullmatch(v.strip()):
                nct = v.strip().upper()
                found.append(("nct", nct, identifier_url("nct", nct), f"nct:{nct}"))
        if found:
            kind, key, url, ref = found[0]
            default = f"PMID {key}" if kind == "pmid" else (f"doi:{key}" if kind == "doi" else key)
            out.add(kind, key, label or default, url, ref)
            for other in found[1:]:
                out.alias(other[0], other[3])
        for v in rec.values():
            ref_scan(v, out, budget, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value[:400]:
            ref_scan(v, out, budget, depth + 1)
    elif isinstance(value, str) and len(value) <= 4000:
        if _RE_PASSAGE.match(value):
            out.add("classical", value, value, "", value)
            return
        for m in _RE_PMID.finditer(value):
            out.add("pmid", m.group(1), f"PMID {m.group(1)}", identifier_url("pmid", m.group(1)),
                    f"pmid:{m.group(1)}")
        for m in _RE_DOI.finditer(value):
            doi = m.group(1).rstrip(".,;)")
            out.add("doi", doi, f"doi:{doi}", identifier_url("doi", doi), f"doi:{doi}")
        bare = _RE_BARE_DOI.match(value.strip())
        if bare:
            out.add("doi", bare.group(1), f"doi:{bare.group(1)}",
                    identifier_url("doi", bare.group(1)), f"doi:{bare.group(1)}")
        for m in _RE_NCT.finditer(value):
            nct = m.group(1).upper()
            out.add("nct", nct, nct, identifier_url("nct", nct), f"nct:{nct}")


# ------------------------------------------------------------------------------- odd values


class Colour(enum.Enum):
    RED = 1
    BLUE = "blue"


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


class MyInt(int):
    pass


class MyFloat(float):
    pass


class MyStr(str):
    pass


class MyList(list):
    pass


class MyDict(dict):
    pass


Pair = collections.namedtuple("Pair", "a b")


@dataclasses.dataclass
class Record:
    name: str
    score: float
    tags: list


class WithToDict:
    def to_dict(self):
        return {"pmid": "12345678", "title": "a paper", "n": 3}


class BrokenAsDict:
    def as_dict(self):
        raise RuntimeError("no")

    def __repr__(self):
        return "BrokenAsDict()"


class Arrayish:
    def tolist(self):
        return [1.5, float("nan"), [2, 3]]


class BadRepr:
    def __repr__(self):
        raise ValueError("no repr")


class LongRepr:
    def __repr__(self):
        return "x" * 900


class BadKey:
    def __str__(self):
        raise ValueError("no str")

    def __hash__(self):
        return 7


class Frozen(Mapping):
    def __init__(self, d):
        self._d = dict(d)

    def __getitem__(self, k):
        return self._d[k]

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)


LEAVES = [None, True, False, 0, -1, 2 ** 70, 3.5, -0.0, 1e308, float("nan"), float("inf"), float("-inf"),
          "", "text", "pmid: 12345", "PMID：999", "doi: 10.1000/xyz.1)", "10.1234/abc", "NCT01234567",
          "passage.shanghan.1", "中文 黄芪", "see pmid 77 and doi:10.5555/q and NCT00000001",
          "pmid 31415926 " + "x" * 4100, Colour.RED, Colour.BLUE, Level.HIGH, MyInt(5), MyFloat(2.5),
          MyFloat("nan"), MyStr("doi: 10.1/zz"), b"bytes", bytearray(b"ba"), memoryview(b"mv"),
          dt.datetime(2026, 10, 10, 12, 0, 0), dt.date(2026, 1, 2), PurePosixPath("/a/b"),
          Record("r", 1.25, ["t"]), WithToDict(), BrokenAsDict(), Arrayish(), BadRepr(), LongRepr(),
          decimal.Decimal("1.5"), complex(1, 2), {1, 2, 3}, frozenset({"b", "a"}), {"x", 1},
          Pair(1, "pmid 4242"), (), [], {}]
KEYS = ["k", "pmid", "doi", "title", "id", "source", "text", "chapter", "nct_id", 1, 2.5, None, True,
        Colour.RED, Level.LOW, ("t", 1), MyStr("s")]


def tree(rng: random.Random, depth: int = 0) -> Any:
    roll = rng.random()
    if depth > 6 or roll < 0.45:
        return rng.choice(LEAVES)
    if roll < 0.6:
        return [tree(rng, depth + 1) for _ in range(rng.randint(0, 6))]
    if roll < 0.65:
        return tuple(tree(rng, depth + 1) for _ in range(rng.randint(0, 4)))
    if roll < 0.68:
        return MyList(tree(rng, depth + 1) for _ in range(rng.randint(0, 3)))
    container = rng.choice([dict, dict, dict, collections.OrderedDict, MyDict, Frozen,
                            lambda d: types.MappingProxyType(d), lambda d: collections.defaultdict(list, d)])
    return container({rng.choice(KEYS): tree(rng, depth + 1) for _ in range(rng.randint(0, 6))})


def same(a: Any, b: Any) -> bool:
    return json.dumps(a, ensure_ascii=False, allow_nan=False) == json.dumps(b, ensure_ascii=False, allow_nan=False)


# ------------------------------------------------------------------------------------ tests

def test_the_reference_has_every_name_it_uses():
    for v in LEAVES:
        out = ref_jsonable(v)
        assert not (isinstance(out, str) and out.startswith("<unrenderable")) or isinstance(v, BadRepr), repr(v)


def test_every_odd_value_comes_out_as_before():
    for v in LEAVES + [[v] for v in LEAVES] + [{"k": v} for v in LEAVES]:
        assert same(jsonable(v), ref_jsonable(v)), repr(v)[:200]


def test_random_trees_come_out_as_before():
    rng = random.Random(20261010)
    for _ in range(3000):
        v = tree(rng)
        assert same(jsonable(v), ref_jsonable(v)), repr(v)[:300]


def test_cycles_depth_and_unrenderable_keys_as_before():
    loop: list = [1]
    loop.append(loop)
    d: dict = {"a": 1}
    d["self"] = d
    mutual_a: dict = {}
    mutual_b = {"a": mutual_a}
    mutual_a["b"] = mutual_b
    deep: Any = 0
    for _ in range(_MAX_DEPTH + 5):
        deep = [deep]
    deep_dict: Any = 0
    for i in range(_MAX_DEPTH + 5):
        deep_dict = {"k": deep_dict} if i % 2 else [deep_dict]
    shared = [1, 2]
    for v in (loop, d, mutual_a, deep, deep_dict, {"x": shared, "y": shared}, {BadKey(): 1}, [{BadKey(): 1}]):
        assert same(jsonable(v), ref_jsonable(v)), repr(v)[:200]
    assert jsonable({BadKey(): 1}) == "<unrenderable dict>"


def test_a_large_numeric_result_is_unchanged():
    rng = random.Random(5)
    m = [[rng.random() if rng.random() > 0.01 else float("nan") for _ in range(120)] for _ in range(120)]
    v = {"names": [f"s{i}" for i in range(120)], "matrix": m, "sites_compared": [[rng.randint(0, 999)] * 120] * 120}
    assert same(jsonable(v), ref_jsonable(v))
    assert envelope.canonical_json(v) == json.dumps(ref_jsonable(v), sort_keys=True, separators=(",", ":"),
                                                    ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("budget", [1, 2, 3, 7, 50, 400, 20_000])
def test_the_citation_scan_cites_the_same_records_with_the_same_budget_left(budget):
    rng = random.Random(budget)
    for _ in range(400):
        v = ref_jsonable(tree(rng))
        records = [{"pmid": str(rng.randint(1, 99999)), "doi": f"10.{rng.randint(1000, 9999)}/x", "title": "t"},
                   {"id": "passage.jingui.3", "source": "金匮要略", "chapter": "第三"},
                   {"nct_id": "nct01234567", "name": ["a list title"]}, {"PMID": 12, "DOI": "10.1/a"}]
        v = [v, rng.choice(records), [rng.choice(records)] * rng.randint(0, 3), list(range(rng.randint(0, 500)))]
        new_out, ref_out = _Citations(), _Citations()
        new_budget, ref_budget = [budget], [budget]
        envelope._scan(v, new_out, new_budget)
        ref_scan(v, ref_out, ref_budget)
        assert new_out.items == ref_out.items
        assert new_budget == ref_budget


def test_extract_citations_on_a_real_shaped_result():
    result = {"records": [{"pmid": "31415926", "title": "黄芪 trial"}, {"doi": "10.1000/abc", "title": "x"}],
              "notes": "see NCT00000002", "matrix": [[0.5] * 50] * 50}
    cites = envelope.extract_citations(result)
    assert [c["id"] for c in cites] == ["E1", "E2", "E3"]
    assert [c["kind"] for c in cites] == ["pmid", "doi", "nct"]
