"""Biomni's tools, admitted only where a reviewed binding verifies against the tree.

Biomni keeps each area's tools in two files: ``biomni/tool/tool_description/<area>.py``,
a list of dicts naming each tool and its parameters for the agent's prompt, and
``biomni/tool/<area>.py``, where the functions are defined. The capability catalogue
indexed the first, and an entrypoint derived from it named the description module, which
defines no function at all. Upstream renames a function, moves it, or changes its
parameters, and a derived entrypoint goes on naming whatever the description says.

``BiomniProvider`` is given a Biomni source tree and the reviewed bindings
(``registry/implementation_bindings.yaml``), and admits a tool only when its binding holds
for that tree, statically — Biomni is never imported, because importing upstream code to
find out whether it is the code you meant to import is the step this exists to make safe:

* the tree is at the commit the binding pins (read from ``.git`` without running git, or
  declared by the caller for a tree without git metadata, and refused when it is neither);
* the implementation is Biomni's own code under ``biomni/``, and not a description file;
  the description, when the binding names one, is one;
* the function is defined at module level of the implementation file, is not rebound or
  decorated afterwards, and has exactly the binding's parameters (``bindings``);
* the description names the function and lists the parameters the implementation takes,
  so a caller that learnt the tool from its description calls it with arguments it accepts.

Manifests are emitted for verified tools only, with ``entrypoint_basis="verified"``, the
implementation and description paths kept apart, the requirements read from the module's
and the function's own imports, and the implementation's licence from the binding. Every
other binding is recorded as a ``Refusal`` with its reason. A tool is never admitted by a
weaker check because the stronger one could not run.

The integration mode is ``vendor``: the python backend imports the upstream package into
this process, which is what the catalogue's ``vendor`` rows mean (``infer_backend``).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping

from ..runtime.component import (ComponentManifest, LicenseSpec, Provider, Requirements,
                                 RuntimeSpec)
from .base import Provider as ProviderBase
from .bindings import (ImplementationBinding, check_implementation, load_bindings,
                       render_signature, tree_commit)

__all__ = ["BiomniProvider", "BiomniVerification", "Refusal", "PROJECT", "TOOL_DIR",
           "DESCRIPTION_DIR"]

PROJECT = "Biomni"
TOOL_DIR = "biomni/tool"
DESCRIPTION_DIR = "biomni/tool/tool_description"
_VARIADIC = ("var_positional", "var_keyword")


@dataclass(frozen=True)
class Refusal:
    """A binding that did not verify, and why."""

    component: str
    reason: str


@dataclass(frozen=True)
class BiomniVerification:
    """One pass over the bindings: what was admitted, what was refused, against what."""

    commit: str
    commit_source: str                        # "git" | "declared" | ""
    manifests: tuple[ComponentManifest, ...]
    refusals: tuple[Refusal, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"commit": self.commit, "commit_source": self.commit_source,
                "verified": [m.id for m in self.manifests],
                "refused": {r.component: r.reason for r in self.refusals}}


class BiomniProvider(ProviderBase):
    """Yields a ``python`` component for each Biomni binding that verifies statically."""

    name = "biomni"

    def __init__(self, root: str | Path, *,
                 bindings: Mapping[str, ImplementationBinding]
                 | Iterable[ImplementationBinding] | None = None,
                 commit: str = "") -> None:
        self.root = Path(root)
        if bindings is None:
            bindings = load_bindings()
        values = bindings.values() if isinstance(bindings, Mapping) else bindings
        self.bindings = tuple(sorted((b for b in values if b.project == PROJECT),
                                     key=lambda b: b.component))
        self.declared_commit = commit.strip()
        self.refusals: tuple[Refusal, ...] = ()

    def available(self) -> bool:
        return (self.root / TOOL_DIR).is_dir()

    def discover(self) -> Iterator[ComponentManifest]:
        """The verified manifests; ``self.refusals`` holds the rest once this has run."""
        result = self.verify()
        self.refusals = result.refusals
        yield from result.manifests

    # ------------------------------------------------------------------ verify
    def verify(self) -> BiomniVerification:
        commit, source, problem = self._tree_commit()
        version = _version(self.root)
        manifests: list[ComponentManifest] = []
        refusals: list[Refusal] = []
        for binding in self.bindings:
            reason, manifest = (problem, None) if problem else self._admit(
                binding, commit, version)
            if manifest is None:
                refusals.append(Refusal(binding.component, reason))
            else:
                manifests.append(manifest)
        return BiomniVerification(commit, source, tuple(manifests), tuple(refusals))

    def _tree_commit(self) -> tuple[str, str, str]:
        """(commit, how it is known, problem). A tree whose commit cannot be established
        admits nothing: every binding is pinned, and "probably that commit" is not one."""
        read = tree_commit(self.root)
        declared = self.declared_commit
        if read and declared and read != declared:
            return "", "", (f"the tree's git metadata says {read} and the caller declared "
                            f"{declared}; one of them is wrong")
        if read:
            return read, "git", ""
        if declared:
            return declared, "declared", ""
        return "", "", ("the tree carries no git metadata and no commit was declared, so it "
                        "cannot be matched to the commit the bindings pin")

    def _admit(self, b: ImplementationBinding, commit: str, version: str
               ) -> tuple[str, ComponentManifest | None]:
        if b.commit != commit:
            return (f"the binding pins {b.commit} and the tree is at {commit}; re-verify the "
                    "binding at the new commit before running it"), None
        layout = _layout_problem(b)
        if layout:
            return layout, None
        check = check_implementation(self.root, b)
        if not check.ok:
            return "; ".join(check.reasons), None
        entry, problem = _description_entry(self.root, b)
        if problem:
            return problem, None
        return "", ComponentManifest(
            id=b.component, kind="tool", name=b.function, version=version or b.commit[:12],
            description=str(entry.get("description") or "")[:400],
            domain=PurePosixPath(b.implementation_path).stem,
            provider=Provider(project=PROJECT, commit=b.commit, repo=b.repo,
                              source_path=b.implementation_path,
                              description_path=b.description_path),
            runtime=RuntimeSpec(backend="python", entrypoint=b.entrypoint,
                                entrypoint_basis="verified"),
            inputs={"parameters": [{"name": p.name, "kind": p.kind,
                                    "required": not p.default and p.kind not in _VARIADIC}
                                   for p in b.signature]},
            requires=Requirements(python=check.imports),
            license=LicenseSpec(spdx=b.licence.spdx, integration_mode="vendor",
                                record=f"binding:{b.component}"),
            signature=render_signature(b.signature))


def _layout_problem(b: ImplementationBinding) -> str:
    """Biomni's own rules: the implementation is Biomni code and not a description."""
    impl = PurePosixPath(b.implementation_path)
    if not b.component.startswith("biomni."):
        return f"component {b.component!r} is not a Biomni component id (biomni.<kind>.<name>)"
    if not impl.is_relative_to("biomni"):
        return f"implementation path {impl} is outside the biomni package"
    if impl.is_relative_to(DESCRIPTION_DIR):
        return (f"implementation path {impl} is a tool description; descriptions say what "
                f"a tool is and the function lives under {TOOL_DIR}/")
    if b.description_path and not PurePosixPath(b.description_path).is_relative_to(
            DESCRIPTION_DIR):
        return (f"description path {b.description_path} is not under {DESCRIPTION_DIR}/, "
                "where Biomni keeps tool descriptions")
    return ""


def _description_entry(root: Path, b: ImplementationBinding) -> tuple[dict[str, Any], str]:
    """The description dict naming the function, and a problem if it does not hold.

    Read with ``ast.literal_eval`` on the dict node, so the description module is parsed and
    never run. A binding without a description path has nothing to compare and passes.
    """
    if not b.description_path:
        return {}, ""
    path = root / b.description_path
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except FileNotFoundError:
        return {}, f"description file {b.description_path} does not exist in the tree"
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as exc:
        return {}, f"{b.description_path} does not parse: {type(exc).__name__}: {exc}"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        if not any(isinstance(k, ast.Constant) and k.value == "name"
                   and isinstance(v, ast.Constant) and v.value == b.function
                   for k, v in zip(node.keys, node.values, strict=True)):
            continue
        try:
            entry = ast.literal_eval(node)
        except ValueError:
            return {}, (f"the description of {b.function} in {b.description_path} is not a "
                        "literal and cannot be read without running it")
        return entry, _parameter_disagreement(entry, b)
    return {}, f"{b.description_path} does not describe {b.function}"


def _parameter_disagreement(entry: Mapping[str, Any], b: ImplementationBinding) -> str:
    """Whether the description's required and optional parameters are the function's."""
    def names(key: str) -> set[str]:
        return {str(p.get("name")) for p in entry.get(key) or () if isinstance(p, Mapping)}

    described = (names("required_parameters"), names("optional_parameters"))
    taken = ({p.name for p in b.signature if not p.default and p.kind not in _VARIADIC},
             {p.name for p in b.signature if p.default})
    if described == taken:
        return ""
    return (f"the description of {b.function} lists required {sorted(described[0])} and "
            f"optional {sorted(described[1])}; the implementation takes required "
            f"{sorted(taken[0])} and optional {sorted(taken[1])}")


def _version(root: Path) -> str:
    """``biomni.version.__version__``, read statically; "" when the tree does not say."""
    path = root / "biomni" / "version.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return ""
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and any(isinstance(t, ast.Name) and t.id == "__version__"
                        for t in node.targets)):
            return node.value.value
    return ""
