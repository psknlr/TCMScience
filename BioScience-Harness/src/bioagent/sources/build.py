"""Build source snapshots from raw files: parse, gate, hash, publish.

``build_source`` builds one source; ``build_gold`` builds the herb layer and the three
natural-product sources restricted to the species of 葛根芩连汤, then checks the gold
standard — every herb's marker compound in one of its source species — across them.
``scripts/build_source_snapshots.py`` is the command-line entry point.
"""

from __future__ import annotations

import gzip
import hashlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Iterable

from . import cards as cards_module
from . import herbs as herb_layer
from . import schema
from .cards import card as source_card
from .composition import herb_composition
from .parsers import (bindingdb, cmaup, common, lotus, npass, opentargets, pubchem_bioassay,
                      reactome, string_db)
from .parsers.common import ParseResult, TaxonFilter
from .snapshot import Snapshot, SnapshotError, build_snapshot, file_hash

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
    primary = raw_dir / pubchem_bioassay.PRIMARY_FILE
    if key == "string":
        return string_db.parse_string(
            raw_dir, proteins=kw["proteins"], mode=kw.get("mode", "induced"),
            primary=pubchem_bioassay.read_primary(primary) if primary.is_file() else None
        ), string_db
    if key == "reactome":
        return reactome.parse_reactome(raw_dir / reactome.FILE,
                                       proteins=kw.get("proteins")), reactome
    if key == "opentargets":
        return opentargets.parse_opentargets(kw["path"]), opentargets
    if key == "pubchem_bioassay":
        if not primary.is_file():
            # Without it the mapping would fall back to whichever accession STRING lists
            # first, which is the primary for under a quarter of human proteins.
            raise SnapshotError(
                f"PubChem targets are mapped to Swiss-Prot primary accessions, which needs "
                f"{pubchem_bioassay.PRIMARY_FILE} in {raw_dir}; fetch it with "
                f"`bioagent datasets fetch {pubchem_bioassay.PRIMARY_FILE}`")
        return pubchem_bioassay.parse_pubchem_bioassay(
            kw["path"], aliases=raw_dir / string_db.FILES["aliases"],
            primary=primary), pubchem_bioassay
    raise KeyError(f"no parser for source {key!r}")


def _scope(version: str) -> str:
    """What a version is a version *of*: the part after ``+`` (a subset, a disease)."""
    return version.split("+", 1)[1] if "+" in version else ""


def _previous(ledger: Any, key: str, version: str, root: str | Path) -> Snapshot | None:
    """The last snapshot of the same source and scope the ledger recorded, as a baseline.

    The drift check compares a build with the one before it, and every builder passed
    ``previous=None``, so the check never ran. The ledger knows what came before. Only a
    snapshot of the same scope is comparable — a four-herb subset against the full file
    is not drift — and one that can no longer be loaded is no baseline at all.
    """
    from .ledger import LedgerError
    from .snapshot import load_snapshot

    try:
        candidates = [e for e in ledger.entries()
                      if e.key == key and _scope(e.version) == _scope(version)]
    except (LedgerError, OSError, ValueError, TypeError):
        return None
    if not candidates:
        return None
    last = candidates[-1]
    try:
        return load_snapshot(root, key, last.version, ledger=ledger, accept_review=True)
    except (SnapshotError, LedgerError):
        return None


def build_source(key: str, raw_dir: str | Path, root: str | Path, *,
                 taxa: TaxonFilter | None = None, version: str | None = None,
                 previous: Snapshot | None = None, ledger: Any = None,
                 **kw: Any) -> Snapshot:
    """Parse ``key``'s raw files in ``raw_dir`` and publish a snapshot under ``root``.

    A restricted build (``taxa``, or BindingDB's ``inchikeys``) is a different dataset from
    the full one, so its version says so (``2.0+subset-<hash>``) and they never share an id.
    """
    result, module = _parse(key, Path(raw_dir), taxa, **kw)
    if key == "pubchem_bioassay":
        # PubChem has no releases: the version is the fetch date, and the compound set
        # queried is the subset
        with gzip.open(kw["path"], "rt", encoding="utf-8") as fh:
            saved = json.load(fh)
        version = version or saved["fetched_at"][:10]
        kw = {**kw, "inchikeys": saved["inchikeys"]}
    if key == "opentargets" and version is None:
        # the Platform release the answer came from, and which disease it is about
        saved = json.loads(Path(kw["path"]).read_text(encoding="utf-8"))
        version = f"{saved['data_version']}+{saved['disease']['id']}"
    version = version or VERSIONS.get(key) or kw.get("release") or "unknown"
    scope = dict(taxa.taxa) if taxa is not None else None
    if key in ("bindingdb", "pubchem_bioassay"):
        scope = sorted(kw["inchikeys"])
    elif key in ("string", "reactome") and kw.get("proteins") is not None:
        scope = [sorted(kw["proteins"]), kw.get("mode", "induced")]
    if scope is not None:
        digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        version = f"{version}+subset-{digest[:8]}"
    if previous is None and ledger is not None:
        candidate = _previous(ledger, key, version, root)
        # A rebuild of the same version from the same raw files is not a new release and
        # has no drift to measure; comparing it with its predecessor would put that
        # predecessor's id into the content and break "same input, same id". A new
        # version, or new bytes under an unchanged one (Reactome's "current"), is compared.
        if candidate is not None and not (
                candidate.version == version
                and candidate.manifest["content"]["raw_files"]
                == {name: file_hash(path) for name, path in sorted(result.raw_files.items())}):
            previous = candidate
    # BindingDB's per-record licence rule is read from its source card, so the card is
    # part of what the parser does and a change to it is a new snapshot.
    code = _code(module, cards_module) if key == "bindingdb" else _code(module)
    return build_snapshot(key=key, version=version, nodes=result.nodes, edges=result.edges,
                          raw_files=result.raw_files, parser=code, root=root,
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
               ledger: Any = None, network: bool = False,
               formulas: Iterable[Any] | None = None,
               drugs: Mapping[str, Any] | None = None) -> GoldBuild:
    """Herb layer + natural-product sources, checked against ``herbs.GOLD``.

    By default the herb layer is 葛根芩连汤 alone and the sources are restricted to its four
    herbs' species. ``formulas`` (``FormulaVersion`` records, e.g. the resolved rows of the
    formula table) are added to the herb layer, and the sources are then restricted to the
    species of every drug in ``drugs`` (default: the whole materia table with verified
    species). The gold markers are checked either way.

    With ``network``, also STRING (the induced subnetwork over the protein targets those
    sources report for the herbs' compounds) and Reactome's full human annotation — the
    whole annotation, because it is the background an enrichment test is drawn against.
    """
    extra = list(formulas or ())
    if extra:
        from .materia import all_drugs
        drug_table = dict(drugs) if drugs is not None else dict(all_drugs())
        drug_table.update(herb_layer.HERBS)
        layer = [herb_layer.GEGEN_QINLIAN, *extra]
        version = "fx-" + hashlib.sha256("\n".join(sorted(
            f.fingerprint for f in layer)).encode()).hexdigest()[:12]
        citation = (herb_layer.CITATION + "; formula compositions from the user-supplied "
                    "formula table (origin and licence not stated)")
        license_ = f"{herb_layer.LICENSE} (herb → species); {layer[-1].license} (compositions)"
        raw = {"herbs.py": Path(herb_layer.__file__)}
        from . import formulas as formula_module, materia as materia_module
        raw.update({"materia.py": Path(materia_module.__file__),
                    "formulas.py": Path(formula_module.__file__)})
        if materia_module.TAXA_FILE.is_file():
            raw["materia_taxa.json"] = materia_module.TAXA_FILE
    else:
        drug_table = dict(herb_layer.HERBS)
        layer = [herb_layer.GEGEN_QINLIAN]
        version = herb_layer.GEGEN_QINLIAN.fingerprint[7:19]
        citation, license_ = herb_layer.CITATION, herb_layer.LICENSE
        raw = {"herbs.py": Path(herb_layer.__file__)}
    taxa = herb_layer.taxon_filter(drug_table.values())
    nodes, edges = herb_layer.herb_rows(layer, drugs=drug_table)
    snapshots = {herb_layer.KEY: build_snapshot(
        key=herb_layer.KEY, version=version, nodes=nodes, edges=edges, raw_files=raw,
        parser=_code(herb_layer), root=root, license=license_, citation=citation,
        ledger=ledger)}
    for key in sources:
        snapshots[key] = build_source(key, raw_dir, root, taxa=taxa, ledger=ledger)
    if network:
        proteins = sorted({x for snap in snapshots.values() for n in snap.nodes
                           if n.get("category") == "target"
                           for x in (n.get("xrefs") or {}).get("uniprot", [])})
        snapshots["string"] = build_source("string", raw_dir, root, proteins=proteins,
                                           ledger=ledger)
        snapshots["reactome"] = build_source("reactome", raw_dir, root, ledger=ledger)
    hits = herb_composition(snapshots.values(), herbs=drug_table)
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
