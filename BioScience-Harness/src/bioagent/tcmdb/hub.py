"""``TCMDataHub``: one API over every TCM database the harness can reach.

    hub = TCMDataHub()                        # data under $BIOAGENT_TCMDB or <data lake>/tcmdb
    hub.catalog(module="M2")                  # the 66 sources: access mode, licence, status
    hub.fetch("itcm"); hub.build("itcm")      # download -> SQLite store + relations
    hub.herb_ingredients("黄芪")              # across every built store
    hub.query("herb2", "clinical_trial", where={"NCT_id": "NCT01699074"})
    hub.live("dcabm_tcm", "herb_blood", names=["SANG YE"])   # a governed live call

Every source in the architecture document is in the catalogue, and its ``access`` field
says how it is reached:
- ``live_api``: a connector (``providers.public_apis_tcm``) called through the runtime,
  under the permission profile's host allowlist;
- ``snapshot``: files downloaded once and queried locally (a dataset named
  ``sources:<key>`` is one the research snapshot pipeline, ``bioagent.sources``, already
  acquires and parses);
- ``manual_import``: a person exports the files and the same loader reads them;
- ``restricted`` / ``unreachable``: nothing to call. The card says why and what a person
  would have to do.

The hub never works around a barrier. It has no code path that logs in, solves a
challenge or reads a page a site serves only to browsers.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .datasets import DATASETS, dataset
from .relations import EVIDENCE, RELATION_KINDS, build_relations
from .store import StoreError, build_store, connect, safe_name

__all__ = ["SourceCard", "TCMDataHub", "catalog", "HubError", "ENV_TCMDB"]

ENV_TCMDB = "BIOAGENT_TCMDB"
ACCESS_MODES = ("live_api", "live_api+snapshot", "snapshot", "manual_import", "restricted",
                "unreachable")


class HubError(RuntimeError):
    """A request the hub cannot serve, with the reason."""


@dataclass(frozen=True)
class SourceCard:
    no: int
    name: str
    modules: tuple[str, ...]
    url: str
    access: str
    connector: str | None
    dataset: str | None
    license: str
    barriers: str
    assessment: str
    checked: str

    @property
    def callable_live(self) -> bool:
        return self.access.startswith("live_api") and bool(self.connector)

    @property
    def has_snapshot(self) -> bool:
        return bool(self.dataset) and self.access in ("snapshot", "live_api+snapshot",
                                                      "manual_import", "restricted")

    def as_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


@lru_cache(maxsize=1)
def catalog() -> tuple[SourceCard, ...]:
    """The source catalogue (``bioagent/data/tcm_source_catalog.json``)."""
    text = resources.files("bioagent.data").joinpath("tcm_source_catalog.json").read_text(
        encoding="utf-8")
    entries = json.loads(text)["entries"]
    return tuple(SourceCard(no=e["no"], name=e["name"], modules=tuple(e["modules"]),
                            url=e["url"], access=e["access"], connector=e.get("connector"),
                            dataset=e.get("dataset"), license=e["license"],
                            barriers=e["barriers"], assessment=e["assessment"],
                            checked=e["checked"]) for e in entries)


def _default_root() -> Path:
    env = os.environ.get(ENV_TCMDB)
    if env:
        return Path(env).expanduser()
    from ..config import data_lake_dir
    return Path(data_lake_dir()) / "tcmdb"


def _like(text: str) -> str:
    """``text`` with LIKE's wildcards escaped (the query says ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _split_names(text: str | None) -> list[str]:
    return [p.strip().casefold() for p in (text or "").split(" | ") if p.strip()]


class TCMDataHub:
    """Catalogue, downloads, local stores and live connectors behind one interface."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else _default_root()
        self._runtime = None

    # ------------------------------------------------------------------ catalogue
    def catalog(self, *, module: str | None = None, access: str | None = None,
                name: str | None = None) -> list[SourceCard]:
        cards = list(catalog())
        if module:
            cards = [c for c in cards if module.upper() in c.modules]
        if access:
            cards = [c for c in cards if c.access == access or c.access.startswith(access)]
        if name:
            needle = name.casefold()
            cards = [c for c in cards if needle in c.name.casefold()]
        return cards

    def card(self, key: str) -> SourceCard:
        """A card by catalogue number, dataset key, connector key or exact name."""
        for c in catalog():
            if key in (str(c.no), c.dataset, c.connector, c.name):
                return c
        raise HubError(f"no source {key!r} in the catalogue")

    # -------------------------------------------------------------------- paths
    def raw_dir(self, key: str) -> Path:
        return self.root / "raw" / key

    def db_path(self, key: str) -> Path:
        return self.root / "db" / f"{key}.sqlite"

    # ------------------------------------------------------------------- status
    def status(self, key: str | None = None) -> list[dict[str, Any]]:
        out = []
        for spec in (dataset(key),) if key else DATASETS:
            raw = self.raw_dir(spec.key)
            files = {f.name: (raw / f.name).exists() for f in spec.files}
            entry: dict[str, Any] = {
                "dataset": spec.key, "name": spec.name, "access": spec.access,
                "files_present": sum(files.values()),
                "files_default": sum(1 for f in spec.files if not f.optional),
                "missing_default": [f.name for f in spec.files
                                    if not f.optional and not files[f.name]],
                "built": self.db_path(spec.key).exists()}
            if entry["built"]:
                with closing(connect(self.db_path(spec.key))) as conn:
                    entry["tables"] = {r["tbl"]: r["rows"] for r in
                                       conn.execute("SELECT tbl, rows FROM _tcmdb_files")}
                    entry["relations"] = {r[0]: r[1] for r in conn.execute(
                        "SELECT kind, count(*) FROM relations GROUP BY kind")}
            out.append(entry)
        return out

    # ------------------------------------------------------------------- fetch
    def fetch(self, key: str, *, include_optional: bool = False, confirm: bool = False,
              log=print) -> list[dict[str, Any]]:
        """Download a dataset's files. Manual datasets raise with their instructions."""
        from ..acquisition.downloader import Downloader, DownloadError
        spec = dataset(key)
        if spec.access == "manual":
            raise HubError(f"{spec.name} cannot be downloaded by a program. "
                           f"{spec.instructions} Directory: {self.raw_dir(key)}")
        dl = Downloader(self.raw_dir(key), timeout_s=120, log=log)
        results = []
        for f in spec.files:
            if f.optional and not include_optional:
                continue
            try:
                r = dl.fetch(f.url, f.name, expected_bytes=f.expected_bytes, confirm=confirm)
                results.append({"file": f.name, "bytes": r.bytes, "checksum": r.checksum,
                                "cached": r.from_cache, "ok": True})
                log(f"{key}: {f.name} {r.bytes} bytes{' (cached)' if r.from_cache else ''}")
            except (DownloadError, OSError) as exc:
                results.append({"file": f.name, "ok": False, "error": str(exc)[:300]})
                log(f"{key}: {f.name} FAILED {exc}")
        return results

    # ------------------------------------------------------------------- build
    def build(self, key: str, *, log=print) -> dict[str, Any]:
        """Load the present files into ``db/<key>.sqlite`` and extract its relations."""
        spec = dataset(key)
        raw = self.raw_dir(key)
        if not raw.exists() or not any(raw.iterdir()):
            hint = spec.instructions if spec.access == "manual" else f"run fetch('{key}')"
            raise HubError(f"{spec.name}: no files in {raw}; {hint}")
        report = build_store(spec, raw, self.db_path(key), log=log)
        conn = connect(self.db_path(key), readonly=False)
        try:
            report["relations"] = build_relations(conn, key)
        finally:
            conn.close()
        return report

    def built(self) -> list[str]:
        return [d.key for d in DATASETS if self.db_path(d.key).exists()]

    # ------------------------------------------------------------------- tables
    def _conn(self, key: str) -> sqlite3.Connection:
        path = self.db_path(key)
        if not path.exists():
            raise HubError(f"{key} is not built; run fetch('{key}') and build('{key}')")
        return connect(path)

    def tables(self, key: str) -> dict[str, list[str]]:
        with closing(self._conn(key)) as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '\\_%' "
                "ESCAPE '\\' ORDER BY name")]
            return {n: [r[1] for r in conn.execute(f'PRAGMA table_info("{n}")')]
                    for n in names}

    def query(self, key: str, table: str, *, where: Mapping[str, Any] | None = None,
              contains: Mapping[str, str] | None = None, columns: Sequence[str] | None = None,
              limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Rows of one source table. ``where`` is exact equality, ``contains`` a
        case-insensitive substring; both are parameterised, and names are checked against
        the table's schema, so nothing a caller passes becomes SQL text."""
        schema = self.tables(key)
        if table not in schema:
            raise HubError(f"{key} has no table {table!r}; have {sorted(schema)}")
        cols = schema[table]

        def checked(name: str) -> str:
            if name not in cols:
                raise HubError(f"{key}.{table} has no column {name!r}; have {cols}")
            return name

        select = ", ".join(f'"{checked(c)}"' for c in columns) if columns else "*"
        clauses, params = [], []
        for col, value in (where or {}).items():
            clauses.append(f'"{checked(col)}" = ?')
            params.append(None if value is None else str(value))
        for col, value in (contains or {}).items():
            clauses.append(f'lower("{checked(col)}") LIKE ? ESCAPE \'\\\'')
            params.append(f"%{_like(str(value).lower())}%")
        sql = f'SELECT {select} FROM "{table}"'
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " LIMIT ? OFFSET ?"
        params += [max(0, min(int(limit), 10_000)), max(0, int(offset))]
        with closing(self._conn(key)) as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    # ---------------------------------------------------------------- relations
    def relations(self, kind: str | None = None, *, subject: str | None = None,
                  object: str | None = None, sources: Iterable[str] | None = None,
                  evidence: Iterable[str] | None = None, contains: bool = False,
                  limit: int = 200) -> list[dict[str, Any]]:
        """Relations across every built store.

        ``subject`` / ``object`` match an id exactly (``pubchem:5280343``,
        ``symbol:TP53``) or one of the names exactly, ignoring case (``黄芪``,
        ``HUANG QI``). ``contains=True`` matches a substring of the names instead, so
        ``黄芪`` then also finds ``炙黄芪`` and ``黄芪鳖甲散``. ``limit`` applies per
        source, so one large source cannot crowd the others out.
        """
        if kind is not None and kind not in RELATION_KINDS:
            raise HubError(f"unknown relation kind {kind!r}; have {sorted(RELATION_KINDS)}")
        ev = set(evidence or ())
        if ev - EVIDENCE:
            raise HubError(f"unknown evidence {sorted(ev - EVIDENCE)}; have {sorted(EVIDENCE)}")
        keys = [k for k in (sources or self.built()) if self.db_path(k).exists()]
        out: list[dict[str, Any]] = []
        for key in keys:
            clauses, params = [], []
            if kind:
                clauses.append("kind = ?")
                params.append(kind)
            if ev:
                clauses.append(f"evidence IN ({', '.join('?' * len(ev))})")
                params.extend(sorted(ev))
            for side, value in (("subject", subject), ("object", object)):
                if value:
                    clauses.append(f"({side}_id = ? OR lower({side}_name) LIKE ? ESCAPE '\\')")
                    params += [value, f"%{_like(value.lower())}%"]
            sql = "SELECT * FROM relations"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            taken = 0
            with closing(self._conn(key)) as conn:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='relations'") \
                        .fetchone():
                    continue                  # a store built before relations existed
                for r in conn.execute(sql, params):
                    row = dict(r)
                    if not contains and not self._exact(row, subject, object):
                        continue
                    out.append(row)
                    taken += 1
                    if taken >= limit:
                        break
        return out

    @staticmethod
    def _exact(row: Mapping[str, Any], subject: str | None, object: str | None) -> bool:
        for side, value in (("subject", subject), ("object", object)):
            if value and row[f"{side}_id"] != value \
                    and value.casefold() not in _split_names(row[f"{side}_name"]):
                return False
        return True

    def herb_ingredients(self, herb: str, **kw: Any) -> list[dict[str, Any]]:
        return self.relations("herb_ingredient", subject=herb, **kw)

    def ingredient_targets(self, ingredient: str, **kw: Any) -> list[dict[str, Any]]:
        return self.relations("ingredient_target", subject=ingredient, **kw)

    def target_ingredients(self, target: str, **kw: Any) -> list[dict[str, Any]]:
        return self.relations("ingredient_target", object=target, **kw)

    def formula_herbs(self, formula: str, **kw: Any) -> list[dict[str, Any]]:
        return self.relations("formula_herb", subject=formula, **kw)

    def herb_formulas(self, herb: str, **kw: Any) -> list[dict[str, Any]]:
        return self.relations("formula_herb", object=herb, **kw)

    def target_diseases(self, target: str, **kw: Any) -> list[dict[str, Any]]:
        return self.relations("target_disease", subject=target, **kw)

    def evidence_for(self, subject: str, **kw: Any) -> list[dict[str, Any]]:
        """Clinical trials, meta-analyses and papers recorded for a herb, formula or
        ingredient."""
        out: list[dict[str, Any]] = []
        for kind in ("subject_clinical_trial", "subject_meta_analysis", "subject_reference"):
            out += self.relations(kind, subject=subject, **kw)
        return out

    # --------------------------------------------------------------------- live
    def live(self, connector: str, operation: str, **arguments: Any) -> Any:
        """Call a live connector through the governed runtime.

        The call passes the permission profile (``biomedical-research``): the host must
        be in its allowlist and in the connector's declared hosts, and the HTTP backend
        applies the per-host rate limit. The result is the runtime's ``CallResult``.
        """
        from ..providers.public_apis import BY_KEY
        from ..psh.arguments import ArgumentError, arguments_for
        from ..runtime.agentspec import AgentSpec
        source = BY_KEY.get(connector)
        if source is None:
            raise HubError(f"no live connector {connector!r}")
        try:
            kwargs = arguments_for({"operation": operation, **arguments}, source=source,
                                   component_id=connector)
        except ArgumentError as exc:
            raise HubError(str(exc)) from None
        if self._runtime is None:
            from ..psh.assembly import default_runtime
            self._runtime = default_runtime(catalogue=False, native_tools=False, skills=False)
        spec = AgentSpec(name="tcmdb", permission_profile="biomedical-research")
        return self._runtime.invoke(f"public.connector.{connector}", spec=spec, **kwargs)


__all__ += ["ACCESS_MODES", "StoreError", "safe_name"]
