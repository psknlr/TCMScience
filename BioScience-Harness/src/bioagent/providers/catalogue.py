"""Provider that turns the v1 capability catalogue into component manifests.

The catalogue stays valuable — it is the *component index*. What changes is its
status: it no longer implies executability. This provider maps each row to a
manifest, infers the execution backend from the row's integration mode and kind,
and attaches the real python-import requirements read from the upstream source
so the resolver can decide READY vs UNAVAILABLE honestly.

Two facts on a row are about the row, not about the code that would run, and the
manifest keeps them apart:

* **the source path** is where the component was found. For Biomni that is the tool's
  description file, so a python entrypoint derived from it names a module with no
  function in it. A reviewed binding (``registry/implementation_bindings.yaml``) supplies
  the entrypoint and the implementation path instead, and the description path is kept
  as its own field. Without one the derived entrypoint stays on the manifest, marked
  ``entrypoint_basis="derived"``: the component is still indexed and searchable, and the
  resolver reports why it will not run rather than the registry silently losing it.
* **the licence column** is, for an aggregator's rows, the aggregator's licence: Biomni
  lists other projects' packages and the catalogue gave each one Biomni's Apache-2.0.
  It stays on the manifest as ``license.catalogue``; ``license.spdx`` comes from the
  binding or a reviewed licence record (``registry/licence_records.yaml``) when there is
  one, and ``license.record`` names it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Iterable, Iterator, Mapping

from ..runtime.component import (ComponentManifest, LicenseSpec, Permissions,
                                 Provider, Requirements, RuntimeSpec, Validation)
from .bindings import ImplementationBinding, load_bindings

if TYPE_CHECKING:
    from ..licences import LicenceRecords

_SLUG = re.compile(r"[^a-z0-9_.-]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", str(text).strip().lower()).strip("-") or "unnamed"


#: project -> import root for python-backed tools
PROJECT_IMPORT_ROOT = {
    "Biomni": "biomni.tool",
    "STELLA": "stella",
    "GenoMAS": "genomas",
    "AutoBA": "autoba",
    "Agentomics": "agentomics",
    "BioMedArena": "harness.tools",
    "GeneAgent": "geneagent",
}


#: Directory names that hold a package but are not part of its import path.
_NON_PACKAGE_DIRS = ("src", "lib", "python", "packages")


def module_for_source(project: str, source_path: str) -> str:
    """Derive an importable module path from a catalogue source path.

    The previous derivation was `f"{root}.{Path(rel).stem}"`, which kept only the
    filename and discarded every intermediate package, so
    `Biomni/biomni/tool/tool_description/pharmacology.py` produced
    `biomni.tool.pharmacology` — a module that does not exist. Across the bundled
    catalogue the great majority of python-backed components disagreed with their
    own `source_paths` this way, which is a large fraction of the python
    capability surface, not an edge case.

    The path is now used in full: strip the repository directory and any
    non-package source root, then join what remains. `PROJECT_IMPORT_ROOT` acts
    as an anchor — if the declared root already appears in the path the module
    starts there, otherwise the root is prefixed onto the derived path.

    This is still a derivation, and a derivation can be wrong when a project's
    on-disk layout differs from its import layout. `scripts/entrypoint_census.py`
    measures how many entrypoints actually import, so the error rate is reported
    rather than assumed.
    """
    root = PROJECT_IMPORT_ROOT.get(project, slug(project))
    rel = (source_path or "").split(";")[0].strip()
    if not rel.endswith(".py"):
        return root

    parts = [p for p in Path(rel).with_suffix("").parts if p not in (".", "/")]
    if parts and parts[0].lower() in (project.lower(), slug(project)):
        parts = parts[1:]                       # drop the repository directory
    while parts and parts[0].lower() in _NON_PACKAGE_DIRS:
        parts = parts[1:]                       # drop src/ and friends
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return root

    anchor = root.split(".")[0]
    lowered = [p.lower() for p in parts]
    if anchor.lower() in lowered:
        return ".".join(parts[lowered.index(anchor.lower()):])
    return ".".join([root, *parts])


def infer_backend(kind: str, integration_mode: str, native_connectors: Iterable[str],
                  project: str) -> str:
    """Choose an execution backend from catalogue facts."""
    ncs = [c for c in native_connectors if c]
    if kind == "dataset":
        return "dataset"
    if kind in ("database", "connector"):
        return "mcp" if ncs else "http"
    if kind in ("benchmark", "software", "agent_role", "skill"):
        # These are declarative in the catalogue: no invocable entrypoint exists.
        return "none"
    if kind == "tool":
        if integration_mode == "vendor" and project in PROJECT_IMPORT_ROOT:
            return "python"
        if ncs:
            return "mcp"
        return "subprocess"
    return "none"


_IMPORT_CACHE: dict[tuple[str, float], tuple[str, ...]] = {}


def scan_module_imports(source_root: Path, rel_path: str) -> tuple[str, ...]:
    """Read the third-party top-level imports of the module a tool lives in.

    Memoized on (path, mtime): Biomni's 224 tools live in 22 modules, so the
    un-memoized version parsed each module ~10 times per registry build.
    """
    p = source_root / rel_path
    if not p.is_file() or p.suffix != ".py":
        return ()
    try:
        key = (str(p), p.stat().st_mtime)
    except OSError:
        return ()
    if key in _IMPORT_CACHE:
        return _IMPORT_CACHE[key]
    result = _scan_uncached(p)
    _IMPORT_CACHE[key] = result
    return result


def _scan_uncached(p: Path) -> tuple[str, ...]:
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return ()
    import sys as _sys
    std = set(_sys.stdlib_module_names)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return tuple(sorted(m for m in found if m not in std and not m.startswith("_")))


def _in_repo_dir(catalogue_path: str, project: str, repo_path: str) -> str:
    """``repo_path`` under the repository directory the catalogue's paths start with.

    Catalogue paths are relative to the directory holding every upstream checkout
    (``Biomni/biomni/...``) and a binding's are relative to its repository, so a bound
    manifest takes the same prefix — and ``scan_module_imports`` reads the implementation
    rather than the description it read before.
    """
    head = PurePosixPath(catalogue_path).parts[:1]
    if not repo_path or not head or head[0].lower() != project.lower():
        return repo_path
    return str(PurePosixPath(head[0], repo_path))


class CatalogueProvider:
    """Discovers components from the unified capability catalogue.

    ``bindings`` and ``licences`` default to the reviewed files the package ships; pass
    ``{}`` and ``LicenceRecords()`` to see the catalogue alone.
    """

    name = "catalogue"

    def __init__(self, rows: Iterable[Mapping], repos_root: Path | None = None,
                 commits: Mapping[str, str] | None = None,
                 bindings: Mapping[str, ImplementationBinding] | None = None,
                 licences: LicenceRecords | None = None) -> None:
        self.rows = list(rows)
        self.repos_root = Path(repos_root) if repos_root else None
        self.commits = dict(commits or {})
        self.bindings = dict(load_bindings() if bindings is None else bindings)
        if licences is None:
            from ..licences import LicenceRecords
            licences = LicenceRecords.load()
        self.licences = licences

    def discover(self) -> Iterator[ComponentManifest]:
        for row in self.rows:
            yield self._to_manifest(row)

    # ------------------------------------------------------------------ mapping
    def _to_manifest(self, row: Mapping) -> ComponentManifest:
        def s(key: str, default: str = "") -> str:
            v = row.get(key, default)
            return "" if v is None else str(v)

        def lst(key: str) -> tuple[str, ...]:
            raw = s(key)
            if not raw or raw.lower() == "nan":
                return ()
            return tuple(x.strip() for x in raw.split(";") if x.strip())

        kind = s("kind", "tool")
        name = s("name")
        projects = lst("contributing_projects")
        primary = projects[0] if projects else ""
        # composite labels such as "ClawBio+K-Dense"
        if "+" in primary:
            primary = primary.split("+")[0]
        licenses = lst("licenses")
        mode_raw = s("integration_mode", "federated")
        mode = {"adapter-only": "federated"}.get(mode_raw, mode_raw)
        ncs = lst("native_connectors")
        source_paths = lst("source_paths")
        rel = source_paths[0] if source_paths else ""

        backend = infer_backend(kind, mode, ncs, primary)
        cid = f"{slug(primary) or 'unknown'}.{kind}.{slug(name)}"
        entrypoint = ""
        server = ""
        basis = ""
        provider = Provider(project=primary, commit=self.commits.get(primary, ""),
                            source_path=rel)
        binding = self.bindings.get(cid) if backend == "python" else None
        if binding is not None:
            entrypoint, basis = binding.entrypoint, "bound"
            provider = Provider(
                project=primary, commit=binding.commit, repo=binding.repo,
                source_path=_in_repo_dir(rel, primary, binding.implementation_path),
                description_path=_in_repo_dir(rel, primary, binding.description_path))
        elif backend == "python":
            entrypoint, basis = f"{module_for_source(primary, rel)}:{name}", "derived"
        elif backend == "mcp":
            server = ncs[0] if ncs else ""
            entrypoint = name

        py_reqs: tuple[str, ...] = ()
        if backend == "python" and self.repos_root and provider.source_path:
            py_reqs = scan_module_imports(self.repos_root, provider.source_path)

        deps = lst("external_deps")
        hosts = tuple(d.lower() for d in deps if "." in d and " " not in d)

        return ComponentManifest(
            id=cid, kind=kind, name=name,
            description=s("description")[:400], domain=s("domain"),
            omics_type=s("omics_type", "general"),
            provider=provider,
            runtime=RuntimeSpec(backend=backend, entrypoint=entrypoint, server=server,
                                entrypoint_basis=basis),
            requires=Requirements(python=py_reqs,
                                  datasets=(name,) if kind == "dataset" else ()),
            permissions=Permissions(network=hosts,
                                    subprocess=(backend == "subprocess")),
            license=self._licence(cid, licenses, s("licenses"), mode, binding),
            validation=Validation(),
            offline_capable=(kind == "dataset"),
            native_connectors=ncs,
            signature=s("signature"),
        )

    def _licence(self, cid: str, licenses: tuple[str, ...], raw: str, mode: str,
                 binding: ImplementationBinding | None) -> LicenseSpec:
        """The implementation's licence when a reviewed source states it; the row's own
        otherwise. The row's value is kept either way, as provenance of the row."""
        catalogue = raw.strip() if raw and raw.strip().lower() != "nan" else ""
        record = self.licences.code_for_component(cid)
        if binding is not None:
            spdx, record_id = binding.licence.spdx, f"binding:{cid}"
        elif record is not None:
            spdx, record_id = record.spdx, record.id
        else:
            spdx, record_id = (licenses[0] if licenses else "NONE"), ""
        return LicenseSpec(spdx=spdx, integration_mode=mode, catalogue=catalogue,
                           record=record_id)
