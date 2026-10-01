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
import time
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .datasets import DATASETS, dataset
from .relations import EVIDENCE, OUTCOMES, RELATION_KINDS, build_relations, relations_digest
from .rowkit import COLUMNS
from .spec import allows_commercial, licence_class
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
    #: "architecture" for the 66 sources of the architecture document; the review a
    #: source was added from otherwise.
    origin: str = "architecture"
    commercial_use: str = "unknown"            # allowed | forbidden | unknown

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
                            checked=e["checked"], origin=e.get("origin", "architecture"),
                            commercial_use=e.get("commercial_use", "unknown"))
                 for e in entries)


def _default_root() -> Path:
    env = os.environ.get(ENV_TCMDB)
    if env:
        return Path(env).expanduser()
    from ..config import data_lake_dir
    return Path(data_lake_dir()) / "tcmdb"


def _like(text: str) -> str:
    """``text`` with LIKE's wildcards escaped (the query says ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _symmap_id(value: Any) -> str:
    """SymMap's files number herbs 1, 2, ...; its pages and query endpoint say SMHB00001."""
    text = str(value).strip()
    return f"SMHB{int(text):05d}" if text.isdigit() else text


def _looks_like_html(path: Path) -> bool:
    """Whether a downloaded file is an HTML page (checked on its first bytes)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return False
    if head[:2] == b"\x1f\x8b" or head[:4] == b"PK\x03\x04":
        return False                                # gzip / zip
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return text.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


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
            files = ({p.name: True for p in raw.glob("*.json")} if spec.access == "live"
                     and raw.exists() else {f.name: (raw / f.name).exists() for f in spec.files})
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
        if spec.access == "live":
            raise HubError(f"{spec.name} is filled per entity: use hub.enrich(<name>) or "
                           f"hub.enrich_{'symmap' if key == 'symmap_api' else 'herb'}([...])")
        dl = Downloader(self.raw_dir(key), timeout_s=120, log=log)
        results = []
        for f in spec.files:
            if f.optional and not include_optional:
                continue
            try:
                r = dl.fetch(f.url, f.name, expected_bytes=f.expected_bytes, confirm=confirm)
                if not f.html_ok and _looks_like_html(self.raw_dir(key) / f.name):
                    (self.raw_dir(key) / f.name).unlink(missing_ok=True)
                    raise HubError(f"{f.url} returned an HTML page (a login, challenge or "
                                   "error page), not the file; it was not kept")
                results.append({"file": f.name, "bytes": r.bytes, "checksum": r.checksum,
                                "cached": r.from_cache, "ok": True})
                log(f"{key}: {f.name} {r.bytes} bytes{' (cached)' if r.from_cache else ''}")
            except (DownloadError, OSError, HubError) as exc:
                results.append({"file": f.name, "ok": False, "error": str(exc)[:300]})
                log(f"{key}: {f.name} FAILED {exc}")
        return results

    # ------------------------------------------------------------------- build
    def build(self, key: str, *, log=print) -> dict[str, Any]:
        """Load the present files into ``db/<key>.sqlite`` and extract its relations."""
        spec = dataset(key)
        raw = self.raw_dir(key)
        if not raw.exists() or not any(raw.iterdir()):
            hint = (spec.instructions if spec.access == "manual"
                    else "run hub.enrich(<name>) first" if spec.access == "live"
                    else f"run fetch('{key}')")
            raise HubError(f"{spec.name}: no files in {raw}; {hint}")
        if spec.access == "live":
            from .live import build_live_store
            report = build_live_store(raw, self.db_path(key), license=spec.license)
        else:
            report = build_store(spec, raw, self.db_path(key), log=log)
        conn = connect(self.db_path(key), readonly=False)
        try:
            report["relations"] = build_relations(conn, key)
            report["unresolved"] = conn.execute("SELECT count(*) FROM unresolved").fetchone()[0]
            report["relations_digest"] = relations_digest(conn)
            conn.execute("CREATE TABLE IF NOT EXISTS _tcmdb_build (key TEXT, value TEXT)")
            conn.execute("DELETE FROM _tcmdb_build")
            negatives = sorted(r[0] for r in conn.execute(
                "SELECT DISTINCT source FROM relations WHERE outcome != 'positive'"))
            conn.executemany("INSERT INTO _tcmdb_build VALUES (?, ?)", [
                ("relations_digest", report["relations_digest"]),
                ("sources_with_negatives", json.dumps(negatives)),
                ("built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))])
            conn.commit()
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
                  outcomes: Iterable[str] | None = None, commercial: bool = False,
                  limit: int = 200) -> list[dict[str, Any]]:
        """Relations across every built store.

        ``subject`` / ``object`` match an id exactly (``pubchem:5280343``,
        ``symbol:TP53``) or one of the names exactly, ignoring case (``黄芪``,
        ``HUANG QI``). ``contains=True`` matches a substring of the names instead, so
        ``黄芪`` then also finds ``炙黄芪`` and ``黄芪鳖甲散``. ``limit`` applies per
        source, so one large source cannot crowd the others out.

        ``outcomes`` keeps only rows with those outcomes (``positive``, ``negative``,
        ``inconclusive``); by default every row is returned, negative results included.
        ``commercial=True`` keeps only rows whose licence allows commercial reuse of
        derived data (``tcmdb.spec.licence_class``: open or share-alike); rows under a
        non-commercial, no-derivatives or unstated licence are left out.
        """
        if kind is not None and kind not in RELATION_KINDS:
            raise HubError(f"unknown relation kind {kind!r}; have {sorted(RELATION_KINDS)}")
        ev = set(evidence or ())
        if ev - EVIDENCE:
            raise HubError(f"unknown evidence {sorted(ev - EVIDENCE)}; have {sorted(EVIDENCE)}")
        oc = set(outcomes or ())
        if oc - OUTCOMES:
            raise HubError(f"unknown outcome {sorted(oc - OUTCOMES)}; have {sorted(OUTCOMES)}")
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
                    for c in COLUMNS:             # a store built before these columns
                        row.setdefault(c, None)
                    row["outcome"] = row["outcome"] or "positive"
                    if not row["license"]:
                        row["license"] = self._licence(key, row["kind"])
                    if oc and row["outcome"] not in oc:
                        continue
                    if commercial and not allows_commercial(row["license"]):
                        continue
                    if not contains and not self._exact(row, subject, object):
                        continue
                    out.append(row)
                    taken += 1
                    if taken >= limit:
                        break
        return out

    @staticmethod
    def _licence(key: str, kind: str) -> str | None:
        try:
            return dataset(key).licence_of(kind)
        except KeyError:
            return None

    def unresolved(self, key: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """Rows a source gave whose object it could not identify (``rowkit.unresolved``)."""
        with closing(self._conn(key)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='unresolved'") \
                    .fetchone():
                return []
            return [dict(r) for r in conn.execute("SELECT * FROM unresolved LIMIT ?",
                                                  (max(0, int(limit)),))]

    def licences(self) -> list[dict[str, Any]]:
        """Each built dataset's relation kinds with their licence and reuse class."""
        out = []
        for key in self.built():
            spec = dataset(key)
            kinds = spec.relations or tuple(spec.relation_licenses)
            for kind in kinds:
                text = spec.licence_of(kind)
                out.append({"dataset": key, "kind": kind, "license": text,
                            "class": licence_class(text),
                            "commercial": allows_commercial(text)})
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

    # ------------------------------------------------------------- reconciliation
    def consensus(self, kind: str, *, subject: str | None = None, object: str | None = None,
                  **kw: Any) -> dict[str, Any]:
        """Every source's rows of ``kind`` for a subject, reconciled (``tcmdb.consensus``):
        ids unified, copies counted once, evidence kinds kept apart, silence reported."""
        from .consensus import consensus
        if kind not in RELATION_KINDS:
            raise HubError(f"unknown relation kind {kind!r}; have {sorted(RELATION_KINDS)}")
        return consensus(self, kind, subject=subject, object=object, **kw)

    def survey(self, kind: str, *, save: bool = True, **kw: Any) -> dict[str, Any]:
        """How much the sources of one relation kind copy each other (``consensus.survey``).

        Saved under ``<root>/consensus/<kind>.json``. ``consensus()`` then takes its copy
        clusters from this survey rather than from the one query's overlap: on a single
        well-studied subject, independent sources overlap because they converge on the
        truth, which a per-query measure would mistake for copying.
        """
        from .consensus import survey
        result = survey(self, kind, **kw)
        if save:
            path = self.root / "consensus" / f"{kind}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            result["surveyed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return result

    def saved_survey(self, kind: str) -> dict[str, Any] | None:
        path = self.root / "consensus" / f"{kind}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def compare(self, kind: str, subject: str, **kw: Any) -> dict[str, Any]:
        """Per-source object sets for one subject: shared, source-only, overlaps."""
        from .consensus import compare
        return compare(self, kind, subject, **kw)

    # --------------------------------------------------------------- enrichment
    def enrich_symmap(self, entity_ids: Iterable[str], *, related: Sequence[str] | None = None,
                      refresh: bool = False, build: bool = True, log=print) -> list[dict]:
        """Fetch (or reuse cached) SymMap relations of the given entities, then rebuild."""
        from .live import SYMMAP_RELATED, fetch_symmap
        ids = list(entity_ids)
        names = self._names("symmap2", "herb", "Herb_id", ("Chinese_name", "Pinyin_name"), ids)
        out = fetch_symmap(self, ids, related=related or SYMMAP_RELATED, names=names,
                           refresh=refresh, log=log)
        if build and any(r["ok"] for r in out):
            self.build("symmap_api", log=log)
        return out

    def enrich_herb(self, entity_ids: Iterable[str], *, label: str = "Herb",
                    refresh: bool = False, build: bool = True, log=print) -> list[dict]:
        """Fetch (or reuse cached) HERB records of the given entities, then rebuild."""
        from .live import fetch_herb
        ids = list(entity_ids)
        table, id_col, name_cols = (("herb", "Herb_id", ("Herb_cn_name", "Herb_pinyin_name"))
                                    if label == "Herb" else
                                    ("ingredient", "Ingredient_id", ("Ingredient_name",)))
        names = self._names("herb2", table, id_col, name_cols, ids)
        out = fetch_herb(self, ids, label=label, names=names, refresh=refresh, log=log)
        if build and any(r["ok"] for r in out):
            self.build("herb_api", log=log)
        return out

    def enrich(self, herb: str, *, refresh: bool = False, log=print) -> dict[str, Any]:
        """Resolve a herb name in the SymMap and HERB entity tables, then enrich both."""
        ids = self.resolve_herb(herb)
        if not ids["symmap"] and not ids["herb"]:
            raise HubError(f"{herb!r} is not a herb name in the built symmap2 or herb2 "
                           "tables (build them, or pass ids to enrich_symmap/enrich_herb)")
        return {"ids": ids,
                "symmap": self.enrich_symmap(ids["symmap"], refresh=refresh, log=log)
                if ids["symmap"] else [],
                "herb": self.enrich_herb(ids["herb"], refresh=refresh, log=log)
                if ids["herb"] else []}

    def resolve_herb(self, name: str) -> dict[str, list[str]]:
        """SymMap and HERB ids of a herb, by exact Chinese, pinyin or Latin name."""
        out: dict[str, list[str]] = {"symmap": [], "herb": []}
        wanted = name.casefold().replace(" ", "")
        for key, table, id_col, cols, side in (
                ("symmap2", "herb", "Herb_id", ("Chinese_name", "Pinyin_name", "Latin_name"),
                 "symmap"),
                ("herb2", "herb", "Herb_id", ("Herb_cn_name", "Herb_pinyin_name",
                                              "Herb_latin_name"), "herb")):
            if not self.db_path(key).exists():
                continue
            with closing(self._conn(key)) as conn:
                for r in conn.execute(f'SELECT "{id_col}", {", ".join(cols)} FROM "{table}"'):
                    names = {p.strip().casefold().replace(" ", "")
                             for v in r[1:] if v for p in str(v).replace(";", ",").split(",")}
                    if wanted in names:
                        out[side].append(_symmap_id(r[0]) if side == "symmap" else r[0])
        return out

    def _names(self, key: str, table: str, id_col: str, cols: Sequence[str],
               ids: Sequence[str]) -> dict[str, str]:
        if not ids or not self.db_path(key).exists():
            return {}
        # SymMap's files number herbs 1, 2, ...; its pages and endpoint say SMHB00001
        local = {(str(int(i[4:])) if key == "symmap2" and str(i).startswith("SMHB") else i): i
                 for i in ids}
        with closing(self._conn(key)) as conn:
            marks = ", ".join("?" * len(local))
            return {local[r[0]]: " | ".join(str(v) for v in r[1:] if v) for r in conn.execute(
                f'SELECT "{id_col}", {", ".join(cols)} FROM "{table}" '
                f'WHERE "{id_col}" IN ({marks})', list(local))}

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
