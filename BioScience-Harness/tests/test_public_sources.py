"""The connector table, checked offline: every claim it makes about itself holds.

Live behaviour is measured by ``scripts/verify_connectors.py``; this file checks the
things that must be true before a request is ever sent, and that the shipped
verification record covers what the table ships.
"""

from __future__ import annotations

import csv
import urllib.parse
from pathlib import Path

import pytest

from bioagent.backends.http import DEFAULT_RATES
from bioagent.providers.public_apis import (BY_KEY, CORE_SOURCES, SOURCES, PublicAPIProvider,
                                            render_call)
from bioagent.providers.public_apis_ext import EXTENDED_SOURCES

DATA = Path(__file__).resolve().parents[1] / "data"


def test_the_table_grew_and_every_key_is_unique():
    assert len(CORE_SOURCES) == 16
    assert len(EXTENDED_SOURCES) >= 35
    assert len(BY_KEY) == len(SOURCES)


@pytest.mark.parametrize("source", SOURCES, ids=lambda s: s.key)
def test_each_source_is_internally_consistent(source):
    assert source.host == urllib.parse.urlsplit(source.base_url).hostname
    assert source.smoke in {op.name for op in source.operations}
    assert len({op.name for op in source.operations}) == len(source.operations)
    assert source.host in DEFAULT_RATES, f"{source.host} has no declared request rate"
    for op in source.operations:
        rendered = render_call(source.key, op.name)          # from its own example
        assert rendered["method"] in ("GET", "POST")
        text = str(rendered)
        for arg in op.args:
            assert arg in op.example, f"{source.key}.{op.name}: example lacks {arg!r}"
        assert not any("{" + a + "}" in text for a in op.args), "unsubstituted placeholder"


def test_a_missing_required_argument_is_refused_before_any_request():
    with pytest.raises(ValueError, match="requires"):
        BY_KEY["hgnc"].op("symbol").render()


def test_json_bodies_are_templated_like_params():
    rendered = render_call("gprofiler", "gost", query="TP53 BRCA1", organism="hsapiens")
    assert rendered["json_body"]["query"] == "TP53 BRCA1"
    assert rendered["json_body"]["organism"] == "hsapiens"
    assert "REAC" in rendered["json_body"]["sources"]


def test_graphql_list_variables_receive_the_typed_value():
    rendered = render_call("dgidb", "gene_interactions", gene="BRAF")
    assert rendered["variables"] == {"names": "BRAF"}
    assert rendered["method"] == "POST"


def test_every_source_becomes_a_connector_manifest_that_declares_its_host():
    manifests = list(PublicAPIProvider().discover())
    assert len(manifests) == len(SOURCES)
    for m in manifests:
        assert m.runtime.backend == "http"
        assert m.permissions.network, m.id
        assert m.validate() == []


def test_the_shipped_verification_record_covers_every_operation():
    """A source ships because it answered. The CSV is the evidence, and it must match."""
    path = DATA / "connector_live_verification.csv"
    assert path.is_file()
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    recorded = {(r["source"], r["operation"]): r for r in rows}
    for source in SOURCES:
        for op in source.operations:
            row = recorded.get((source.key, op.name))
            assert row is not None, f"{source.key}.{op.name} was never verified"
            assert row["status"] == "SUCCEEDED", (
                f"{source.key}.{op.name} shipped with status {row['status']}: {row['error']}")
    smoke_rows = [r for r in rows if r["smoke"] == "True"]
    assert {r["source"] for r in smoke_rows} == set(BY_KEY)


@pytest.mark.parametrize("name,path", [
    ("黄芩苷", "compound/name/%E9%BB%84%E8%8A%A9%E8%8B%B7/cids/JSON"),
    ("berberine chloride", "compound/name/berberine%20chloride/cids/JSON"),
    ("a#b", "compound/name/a%23b/cids/JSON"),
    ("x?y=1", "compound/name/x%3Fy=1/cids/JSON"),
    ("5%", "compound/name/5%25/cids/JSON"),
])
def test_a_path_argument_is_sent_as_the_name_that_was_asked_about(name, path):
    """A compound name fills its path segment and nothing else.

    Pasted in raw, ``黄芩苷`` could not be sent (the request line is ASCII), a name with
    a space was an invalid URL, ``a#b`` was *sent* as a lookup for ``a`` — the rest is a
    fragment the server never sees — and came back as a successful answer, and ``?``
    opened a query string the operation never declared.
    """
    assert BY_KEY["pubchem"].op("cid_by_name").render(name=name)["path"] == path


def test_every_shipped_example_still_sends_the_request_that_was_verified_live():
    """The encoding changes no request the verification record covers.

    ``/`` and ``>`` are left alone on purpose: DOIs span path segments and HGVS ids carry
    ``>``, and bioRxiv and MyVariant both answer the encoded forms with a 404.
    """
    import re

    def pasted(template, kwargs):
        return re.sub(r"\{(\w+)\}", lambda m: str(kwargs.get(m.group(1), m.group(0))),
                      template)

    for source in SOURCES:
        for op in source.operations:
            example = dict(op.example)
            assert op.render(**example)["path"] == pasted(op.path, example), \
                f"{source.key}.{op.name}"
