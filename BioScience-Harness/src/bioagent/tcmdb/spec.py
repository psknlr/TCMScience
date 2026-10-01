"""What a dataset is made of: its files, their licences, and the relations it yields.

``FileSpec`` and ``DatasetSpec`` live here, apart from the list of datasets, so that a
module adding datasets (``tcmdb.extra``) can import them without importing that list.

Licences are recorded per file when a source licenses its files differently. TM-MC, for
example, publishes some files under CC BY and others under CC BY-NC. Licences are also
recorded per relation kind, so a commercial query can leave out exactly the rows whose
terms do not allow it (``licence_class``, ``TCMDataHub.relations(commercial=True)``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

__all__ = ["FileSpec", "DatasetSpec", "LICENCE_CLASSES", "licence_class",
           "allows_commercial"]


@dataclass(frozen=True)
class FileSpec:
    url: str
    name: str                          # local file name under raw/<dataset>/
    table: str                         # SQLite table it becomes ("" = kept as a file)
    fmt: str = "tsv"                   # tsv | csv | ws (whitespace) | xlsx | parquet |
    #                                    json | jsonl | gmt | ttd | text | raw
    optional: bool = False             # large or rarely needed: fetched only on request
    expected_bytes: int | None = None
    sheet: str | None = None           # xlsx sheet (default: the first)
    columns: tuple[str, ...] = ()      # header for a headerless file
    note: str = ""
    license: str = ""                  # this file's licence, when it differs from the dataset's
    #: Some files are served as HTML (a page a person reads). For every other file an HTML
    #: answer is a login, challenge or error page, and the hub refuses to keep it.
    html_ok: bool = False


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    name: str
    catalog: tuple[int, ...]           # entry numbers in the source catalogue
    homepage: str
    license: str
    files: tuple[FileSpec, ...]
    access: str = "download"           # download | manual | live
    version: str = ""
    notes: str = ""
    instructions: str = ""             # for manual datasets: how a person obtains the files
    relations: tuple[str, ...] = ()    # relation kinds its extractor yields (tcmdb.relations)
    extra: Mapping[str, str] = field(default_factory=dict)
    #: relation kind -> licence, when the file a kind comes from is licensed differently
    #: from the dataset as a whole.
    relation_licenses: Mapping[str, str] = field(default_factory=dict)
    #: allowed | forbidden | unknown, for the dataset as a whole (what its terms say).
    commercial_use: str = "unknown"
    #: upstream databases this dataset redistributes, so the same upstream record is not
    #: counted twice (``tcmdb.consensus``).
    upstream: tuple[str, ...] = ()
    #: id mappings this dataset contributes to ``consensus.Crosswalk``: "compound" or
    #: "gene" -> SQL over the built store returning (id, canonical id) pairs, already
    #: prefixed: ``inchikey:`` (27 characters) for compounds (or ``pubchem:<CID>`` when the
    #: source has no structure; the CID is then resolved to its InChIKey when another store
    #: knows it), ``symbol:`` for human genes.
    crosswalk: Mapping[str, str] = field(default_factory=dict)
    #: the least seconds between two download requests to this dataset's host (its
    #: robots.txt Crawl-delay); ``hub.fetch`` paces every request by it.
    min_interval_s: float = 0.0

    def file(self, name: str) -> FileSpec:
        for f in self.files:
            if f.name == name or f.table == name:
                return f
        raise KeyError(f"{self.key} has no file {name!r}")

    def licence_of(self, kind: str) -> str:
        return self.relation_licenses.get(kind) or self.license


#: From most to least permissive for reuse in a derived product.
LICENCE_CLASSES = ("open", "share-alike", "non-commercial", "no-derivatives", "unknown")

_NC = re.compile(r"\bnc\b|non-?commercial|academic (?:use|purposes|only)|not for commercial|"
                 r"commercial use (?:by arrangement|requires|is not|not permitted)|"
                 r"research use only", re.I)
_ND = re.compile(r"\bnd\b|no-?deriv", re.I)
_OPEN = re.compile(r"\bcc0\b|public domain|\bcc[ -]?by\b|creative commons attribution|"
                   r"\bmit\b|apache|\bbsd\b|\bodc-by\b|\bpddl\b|open government licen", re.I)
_SA = re.compile(r"\bsa\b|share-?alike|\bodbl\b|\bdbcl\b|\bgpl\b", re.I)


def licence_class(text: str | None) -> str:
    """The reuse class of a licence statement, read conservatively.

    A statement that names a non-commercial or no-derivatives term is that class, even
    when it also names an open licence (``CC BY 4.0 (pairs); ids via a CC BY-NC file``).
    A statement that names no recognisable licence ("not stated", "cite the paper") is
    ``unknown``, which is not permission.
    """
    t = (text or "").strip()
    if not t:
        return "unknown"
    if _NC.search(t):
        return "non-commercial"
    if _ND.search(t):
        return "no-derivatives"
    if _OPEN.search(t) or _SA.search(t):
        return "share-alike" if _SA.search(t) else "open"
    return "unknown"


def allows_commercial(text: str | None) -> bool:
    """Whether the licence lets derived data be used commercially (open or share-alike)."""
    return licence_class(text) in ("open", "share-alike")
