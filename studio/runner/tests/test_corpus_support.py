"""Helpers for the corpus tests: a tiny formula table, the open-data fixtures, one tiny
corpus built per session, a fake ``js`` module for the browser's synchronous XHR, and a
loopback HTTP server. Nothing here reads the 14 MB table or needs the network."""

from __future__ import annotations

import contextlib
import gzip
import json
import shutil
import threading
import types
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

STUDIO = Path(__file__).resolve().parents[2]
FIXTURES = STUDIO / "corpus" / "fixtures"
COLUMNS = ("名称", "配方", "出处", "炮制", "功效", "使用方法", "注意")

#: Twelve sheet rows: two 桂枝汤 (one of them the curated formula's name), four 人参散 (more
#: than three, so a name lookup lists candidates), a row naming its 出处 in the name, an empty
#: row, a row with unresolvable ingredients, and a duplicate of row 6 (same id).
TABLE_ROWS: list[tuple[Any, ...]] = [
    ("桂枝汤", "桂枝3两（去皮），芍药3两，甘草2两（炙），生姜3两（切），大枣12枚（擘）。", "《伤寒论》。",
     "上5味，(口父)咀3味。", "外感风寒，汗出恶风。现用于感冒。", "温服1升。", "禁生冷。"),
    ("桂枝汤", "桂枝1两，芍药1两，甘草半两。", "《圣惠》卷九。", "", "伤寒头痛。", "", ""),
    ("人参散", "人参1两，白术1两，茯苓1两。", "《圣惠》卷五。", "", "脾虚。", "", ""),
    ("人参散", "人参2两，黄芪1两。", "《圣惠》卷六。", "", "气虚。", "", ""),
    ("人参散", "人参1两，甘草半两。", "《普济方》卷一。", "", "", "", ""),
    ("人参散", "人参1两，当归1两。", "《千金》卷二。", "", "", "", ""),
    ("黄芪汤", "黄芪2两，人参1两，甘草1两。", "《圣济总录》卷五。", "", "自汗。", "", ""),
    ("补中益气汤（《脾胃论》卷中。）", "黄芪5分，甘草（炙）5分，人参3分，当归身2分，橘皮2分，升麻2分，柴胡2分，白术3分。",
     "《脾胃论》卷中。", "", "", "", ""),
    (None, None, None, None, None, None, None),
    ("怪方", "贝母1两，防葵2两。", "《外台》卷一。", "", "", "", ""),
    ("黄芪汤", "黄芪2两，人参1两，甘草1两。", "《圣济总录》卷五。", "", "自汗。", "", ""),
    ("四物汤", "熟地黄，当归，白芍，川芎各等分。", "《局方》卷九。", "", "", "", ""),
]


def write_table(path: Path, rows: list[tuple[Any, ...]] = TABLE_ROWS) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(COLUMNS))
    for row in rows:
        ws.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _gz(doc: Any) -> bytes:
    return gzip.compress(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8"), 9, mtime=0)


def write_data(directory: Path, *, lotus_source: dict[str, Any] | None = None) -> Path:
    """``lotus-herbs.json.gz`` and ``pathways.json.gz`` in ``directory``: the committed mini
    fixtures when present, else a few synthetic records of the same schemas."""
    directory.mkdir(parents=True, exist_ok=True)
    lotus = FIXTURES / "lotus-mini.json.gz"
    pathways = FIXTURES / "pathways-mini.json.gz"
    if lotus.is_file() and pathways.is_file() and lotus_source is None:
        shutil.copyfile(lotus, directory / "lotus-herbs.json.gz")
        shutil.copyfile(pathways, directory / "pathways.json.gz")
        return directory
    source = lotus_source or {"name": "LOTUS natural products occurrences", "licence": "CC-BY-4.0",
                              "version": "2026-04-13", "citation": "doi:10.7554/eLife.70780",
                              "url": "https://zenodo.org/records/19360665"}
    (directory / "lotus-herbs.json.gz").write_bytes(_gz({
        "schema": "tcmstudio.corpus.lotus/1", "source": source,
        "herbs": {"gancao": {"organisms": [{"name": "Glycyrrhiza uralensis", "taxid": "74613",
                                            "matched_by": "taxid"}],
                             "occurrences": [["LPLVUJXQOOQHMX-QWBHMCJMSA-N", 43],
                                             ["AAAAAAAAAAAAAA-UHFFFAOYSA-N", 2]]},
                  "huangqi": {"organisms": [{"name": "Astragalus mongholicus", "taxid": "1424",
                                             "matched_by": "taxid"}],
                              "occurrences": [["BBBBBBBBBBBBBB-UHFFFAOYSA-N", 5]]}},
        "compounds": {"LPLVUJXQOOQHMX-QWBHMCJMSA-N": {"name": "glycyrrhizin", "formula": "C42H62O16",
                                                     "smiles": "C"},
                      "AAAAAAAAAAAAAA-UHFFFAOYSA-N": {"name": "x", "formula": "C", "smiles": "C"},
                      "BBBBBBBBBBBBBB-UHFFFAOYSA-N": {"name": "y", "formula": "C", "smiles": "C"}},
        "unmatched": {"shigao": {"category": "mineral", "reason": "not_an_organism", "species": []}}}))
    if not (directory / "pathways.json.gz").is_file():
        (directory / "pathways.json.gz").write_bytes(_gz({
            "schema": "tcmstudio.corpus.pathways/1",
            "sources": [{"key": "reactome", "name": "Reactome", "licence": "CC0-1.0", "version": "97"},
                        {"key": "hgnc", "name": "HGNC", "licence": "CC0-1.0", "version": "2026-10-06"}],
            "pathways": {"R-HSA-1": {"name": "p1", "members": ["P04217", "P05231"]}},
            "symbol_to_uniprot": {"A1BG": "P04217", "IL6": "P05231"},
            "uniprot_to_symbol": {"P04217": "A1BG", "P05231": "IL6"}}))
    return directory


_BUILT: dict[str, dict[str, Any]] = {}


def tiny_corpus(tmp_path_factory: Any) -> dict[str, Any]:
    """One tiny corpus per session: ``{site, summary, logs, xlsx, data}``."""
    if "tiny" not in _BUILT:
        from tcmstudio.corpus.build import build_corpus
        root = tmp_path_factory.mktemp("corpus-tiny")
        xlsx = write_table(root / "table.xlsx")
        data = write_data(root / "data")
        logs: list[str] = []
        summary = build_corpus(root / "site", xlsx=xlsx, data_dir=data, cache=False,
                               log=logs.append)
        _BUILT["tiny"] = {"site": root / "site", "summary": summary, "logs": logs, "xlsx": xlsx,
                          "data": data, "root": root}
    return _BUILT["tiny"]


def boot_entry(summary: dict[str, Any]) -> dict[str, Any]:
    return {"manifest": f"corpus/{summary['manifest']}", "sha256": summary["sha256"]}


class FakeJS(types.ModuleType):
    """What a Pyodide worker's ``js`` module gives the XHR fetcher: ``XMLHttpRequest.new()``
    that serves files under ``root`` for URLs under ``base``, and ``Uint8Array.new(buf)``
    whose ``to_py()`` is a memoryview. It records every URL requested."""

    def __init__(self, root: Path, base: str) -> None:
        super().__init__("js")
        self.requests: list[str] = []
        outer = self

        class XMLHttpRequest:
            def __init__(self) -> None:
                self.status = 0
                self.response: bytes | None = None
                self.responseType = ""
                self.url = ""

            @classmethod
            def new(cls) -> "XMLHttpRequest":
                return cls()

            def open(self, method: str, url: str, is_async: bool) -> None:
                assert method == "GET" and is_async is False, "the worker reads synchronously"
                self.url = url

            def setRequestHeader(self, name: str, value: str) -> None:   # noqa: N802
                pass

            def send(self) -> None:
                assert self.responseType == "arraybuffer"
                outer.requests.append(self.url)
                path = root / self.url[len(base):] if self.url.startswith(base) else None
                if path is not None and path.is_file():
                    self.status, self.response = 200, path.read_bytes()
                else:
                    self.status = 404

        class Uint8Array:
            def __init__(self, buf: bytes) -> None:
                self.buf = buf

            @classmethod
            def new(cls, buf: bytes) -> "Uint8Array":
                return cls(buf)

            def to_py(self) -> memoryview:
                return memoryview(self.buf)

        self.XMLHttpRequest = XMLHttpRequest
        self.Uint8Array = Uint8Array


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


@contextlib.contextmanager
def serve_directory(directory: Path) -> Iterator[str]:
    """A loopback HTTP server for ``directory``; yields its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
