"""Build source snapshots from raw files: parse, gate, hash, publish.

``build_source`` builds one source; ``build_gold`` builds the herb layer and the three
natural-product sources restricted to the species of 葛根芩连汤, then checks the gold
standard — every herb's marker compound in one of its source species — across them.
``scripts/build_source_snapshots.py`` is the command-line entry point.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

from . import herbs as herb_layer
from . import schema
from .cards import card as source_card
from .composition import herb_composition
from .parsers import bindingdb, cmaup, common, lotus, npass, opentargets, reactome, string_db
from .parsers.common import ParseResult, TaxonFilter
from .snapshot import Snapshot, SnapshotError, build_snapshot

__all__ = ["LOTUS_FILE", "VERSIONS", "build_source", "build_gold", "require_gold", "GoldBuild"]

LOTUS_FILE = "260413_frozen.csv.gz"
VERSIONS = {"npass": "2.0", "cmaup": "2.0", "lotus": "2026-04-13", "string": "12.0",
            # Reactome publishes "current"; the snapshot id still pins the exact file.
            "reactome": "current"}


def _code(*modules: ModuleType) -> str:
    """The parser's code for the snapshot hash: its module plus the shared helpers and
    schema it relies on, so a change to any of them is a new snapshot."""
    return "\n".join(inspect.getsource(m) for m in (*modules, common, schema))


def _parse(key: str, raw_dir: Path, taxa: TaxonFilter | None,
           **kw: Any) -> tuple[ParseResult, ModuleType]:
    if key == "npass":
        return npass.parse_npass(raw_dir, taxa=taxa), npass
    if key == "cmaup":
        return cmaup.parse_cmaup(raw_dir, taxa=taxa), cmaup
    if key == "lotus":
        return lotus.parse_lotus(raw_dir / kw.get("lotus_file", LOTUS_FILE), taxa=taxa), lotus
    if key == "bindingdb":
        return bindingdb.parse_bindingdb(kw["path"], inchikeys=kw["inchikeys"]), bindingdb
    if key == "string":
        return string_db.parse_string(raw_dir, proteins=kw["proteins"],
                                      mode=kw.get("mode", "induced")), string_db
    if key == "reactome":
        return reactome.parse_reactome(raw_dir / reactome.FILE,
                                       proteins=kw.get("proteins")), reactome
    if key == "opentargets":
        return opentargets.parse_opentargets(kw["path"]), opentargets
    raise KeyError(f"no parser for source {key!r}")


def build_source(key: str, raw_dir: str | Path, root: str | Path, *,
                 taxa: TaxonFilter | None = None, version: str | None = None,
                 previous: Snapshot | None = None, ledger: Any = None,
                 **kw: Any) -> Snapshot:
    """Parse ``key``'s raw files in ``raw_dir`` and publish a snapshot under ``root``.

    A restricted build (``taxa``, or BindingDB's ``inchikeys``) is a different dataset from
    the full one, so its version says so (``2.0+subset-<hash>``) and they never share an id.
    """
    result, module = _parse(key, Path(raw_dir), taxa, **kw)
    if key == "opentargets" and version is None:
        # the Platform release the answer came from, and which disease it is about
        saved = json.loads(Path(kw["path"]).read_text(encoding="utf-8"))
        version = f"{saved['data_version']}+{saved['disease']['id']}"
    version = version or VERSIONS.get(key) or kw.get("release") or "unknown"
    scope = dict(taxa.taxa) if taxa is not None else None
    if key == "bindingdb":
        scope = sorted(kw["inchikeys"])
    elif key in ("string", "reactome") and kw.get("proteins") is not None:
        scope = [sorted(kw["proteins"]), kw.get("mode", "induced")]
    if scope is not None:
        digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        version = f"{version}+subset-{digest[:8]}"
    return build_snapshot(key=key, version=version, nodes=result.nodes, edges=result.edges,
                          raw_files=result.raw_files, parser=_code(module), root=root,
                          card=source_card(key), previous=previous,
                          extra={"parse_report": result.report.as_dict()}, ledger=ledger)


@dataclass
class GoldBuild:
    snapshots: dict[str, Snapshot]
    composition: list[dict]
    missing: dict[str, str]

    @property
    def passed(self) -> bool:
        return not self.missing


def build_gold(raw_dir: str | Path, root: str | Path, *,
               sources: Iterable[str] = ("npass", "cmaup", "lotus"),
               ledger: Any = None, network: bool = False) -> GoldBuild:
    """Herb layer + natural-product sources for 葛根芩连汤, checked against ``herbs.GOLD``.

    With ``network``, also STRING (the induced subnetwork over the protein targets those
    sources report for the herbs' compounds) and Reactome's full human annotation — the
    whole annotation, because it is the background an enrichment test is drawn against.
    """
    taxa = herb_layer.taxon_filter()
    nodes, edges = herb_layer.herb_rows()
    snapshots = {herb_layer.KEY: build_snapshot(
        key=herb_layer.KEY, version=herb_layer.GEGEN_QINLIAN.fingerprint[7:19], nodes=nodes,
        edges=edges, raw_files={"herbs.py": Path(herb_layer.__file__)},
        parser=_code(herb_layer), root=root, license=herb_layer.LICENSE,
        citation=herb_layer.CITATION, ledger=ledger)}
    for key in sources:
        snapshots[key] = build_source(key, raw_dir, root, taxa=taxa, ledger=ledger)
    if network:
        proteins = sorted({x for snap in snapshots.values() for n in snap.nodes
                           if n.get("category") == "target"
                           for x in (n.get("xrefs") or {}).get("uniprot", [])})
        snapshots["string"] = build_source("string", raw_dir, root, proteins=proteins,
                                           ledger=ledger)
        snapshots["reactome"] = build_source("reactome", raw_dir, root, ledger=ledger)
    hits = herb_composition(snapshots.values())
    found = {(h.herb, h.compound) for h in hits}
    missing = {herb: f"{name} ({inchikey}) is in none of the herb's source species"
               for herb, (name, inchikey) in herb_layer.GOLD.items()
               if (herb, f"inchikey:{inchikey}") not in found}
    return GoldBuild(snapshots, [h.as_dict() for h in hits], missing)


def require_gold(build: GoldBuild) -> GoldBuild:
    if not build.passed:
        raise SnapshotError("gold standard not reproduced: " + "; ".join(
            f"{h}: {why}" for h, why in sorted(build.missing.items())))
    return build
