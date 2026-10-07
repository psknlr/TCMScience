"""Implementation bindings: which upstream function a catalogue component actually runs.

The capability catalogue records where a component was *found*, and for Biomni that is the
tool's description: ``biomni/tool/tool_description/genomics.py`` lists
``annotate_celltype_scRNA`` with its parameters, and the function itself lives in
``biomni/tool/genomics.py``. ``catalogue.module_for_source`` turned the description path
into ``biomni.tool.tool_description.genomics:annotate_celltype_scRNA``, an entrypoint that
names a module holding a list of dicts. Discovering a candidate from a description is
fine. Executing one needs the implementation named on purpose, by someone who looked.

A binding is that statement, kept in ``registry/implementation_bindings.yaml``: the
component id, the ``module:function`` to import, the upstream repository and the commit it
is pinned to, the implementation file and — kept apart — the description file, the
signature the function must have (each parameter's name, kind and whether it has a
default), and the implementation's own licence with where that claim was checked. The
catalogue's licence for the row is the aggregator's, which is a different fact.

The checks here are static. ``check_implementation`` parses the pinned file with ``ast``
and never imports it, because importing upstream code to find out whether it is the code
you meant to import is the step the binding exists to make safe. ``providers.biomni``
applies them to a Biomni tree, with the rules particular to Biomni's layout.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

__all__ = ["BindingError", "Param", "LicenceClaim", "ImplementationBinding", "PARAM_KINDS",
           "ImplementationCheck", "bindings_path", "load_bindings", "parse_bindings",
           "check_implementation", "render_signature", "signature_of", "tree_commit"]

#: ``inspect.Parameter`` kinds, spelled as the binding file spells them.
PARAM_KINDS: tuple[str, ...] = ("positional_only", "positional_or_keyword", "var_positional",
                                "keyword_only", "var_keyword")

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ENTRYPOINT = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BINDING_KEYS = frozenset({"component", "project", "entrypoint", "repo", "commit",
                           "implementation_path", "description_path", "signature",
                           "licence", "checked_on"})


class BindingError(ValueError):
    """A binding that names something it could not be checked against."""


@dataclass(frozen=True)
class Param:
    """One parameter of the bound function: its name, its kind, whether it has a default."""

    name: str
    kind: str = "positional_or_keyword"
    default: bool = False

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise BindingError(f"parameter name {self.name!r} is not an identifier")
        if self.kind not in PARAM_KINDS:
            raise BindingError(f"parameter kind {self.kind!r} is not one of {PARAM_KINDS}")
        if not isinstance(self.default, bool):
            raise BindingError(f"{self.name}: default is true or false (whether one is "
                               "present)")
        if self.default and self.kind in ("var_positional", "var_keyword"):
            raise BindingError(f"*{self.name} cannot have a default")


def render_signature(params: Iterable[Param]) -> str:
    """``a, b=…, *args, c, **kw`` — a parameter list as Python spells it, for messages."""
    parts: list[str] = []
    params = list(params)
    star_written = False
    for i, p in enumerate(params):
        if (p.kind == "keyword_only" and not star_written
                and not any(q.kind == "var_positional" for q in params)):
            parts.append("*")
            star_written = True
        text = {"var_positional": f"*{p.name}", "var_keyword": f"**{p.name}"}.get(
            p.kind, p.name + ("=…" if p.default else ""))
        parts.append(text)
        if p.kind == "positional_only" and (i + 1 == len(params)
                                            or params[i + 1].kind != "positional_only"):
            parts.append("/")
    return ", ".join(parts)


@dataclass(frozen=True)
class LicenceClaim:
    """The implementation's own licence, and where that was read."""

    spdx: str
    source: str

    def __post_init__(self) -> None:
        if not self.spdx.strip() or not self.source.strip():
            raise BindingError("a licence claim names an SPDX id and where it was checked")


@dataclass(frozen=True)
class ImplementationBinding:
    """A reviewed statement of which function a component runs, pinned to a commit."""

    component: str
    project: str
    entrypoint: str
    repo: str
    commit: str
    implementation_path: str
    description_path: str
    signature: tuple[Param, ...]
    licence: LicenceClaim
    checked_on: str

    def __post_init__(self) -> None:
        problems = []
        if not self.component or " " in self.component:
            problems.append(f"component id {self.component!r}")
        if not self.project:
            problems.append("no project")
        if not _ENTRYPOINT.match(self.entrypoint):
            problems.append(f"entrypoint {self.entrypoint!r} is not module:function")
        if not self.repo.startswith("https://"):
            problems.append(f"repo {self.repo!r} is not an https URL")
        if not _COMMIT.match(self.commit):
            problems.append(f"commit {self.commit!r} is not a full 40-character SHA; a "
                            "branch or tag moves, so it pins nothing")
        for label, path in (("implementation_path", self.implementation_path),
                            ("description_path", self.description_path)):
            if path and not _safe_relative(path):
                problems.append(f"{label} {path!r} is not a relative path inside the tree")
        if not self.implementation_path.endswith(".py"):
            problems.append(f"implementation_path {self.implementation_path!r} is not a "
                            "Python file")
        elif _ENTRYPOINT.match(self.entrypoint) and self.module != _module_of(
                self.implementation_path):
            problems.append(f"entrypoint module {self.module!r} is not the module "
                            f"{self.implementation_path!r} defines "
                            f"({_module_of(self.implementation_path)!r})")
        if self.description_path and self.description_path == self.implementation_path:
            problems.append("the description and the implementation are the same file; "
                            "a binding exists to tell them apart")
        if len({p.name for p in self.signature}) != len(self.signature):
            problems.append("a parameter is named twice")
        if not _ISO_DATE.match(self.checked_on):
            problems.append(f"checked_on {self.checked_on!r} is not an ISO date")
        if problems:
            raise BindingError(f"binding {self.component!r}: " + "; ".join(problems))

    @property
    def module(self) -> str:
        return self.entrypoint.partition(":")[0]

    @property
    def function(self) -> str:
        return self.entrypoint.partition(":")[2]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ImplementationBinding":
        if not isinstance(data, Mapping):
            raise BindingError("a binding must be a mapping")
        unknown = sorted(set(data) - _BINDING_KEYS)
        if unknown:
            raise BindingError(f"binding {data.get('component')!r} has unknown key(s) "
                               f"{unknown}")
        licence = data.get("licence") or {}
        if not isinstance(licence, Mapping) or set(licence) - {"spdx", "source"}:
            raise BindingError(f"binding {data.get('component')!r}: licence is a mapping "
                               "of spdx and source")
        signature = data.get("signature")
        if not isinstance(signature, list):
            raise BindingError(f"binding {data.get('component')!r}: signature is a list "
                               "(empty for a function without parameters)")
        params = []
        for p in signature:
            if not isinstance(p, Mapping) or set(p) - {"name", "kind", "default"}:
                raise BindingError(f"binding {data.get('component')!r}: a parameter is a "
                                   "mapping of name, kind and default")
            params.append(Param(str(p.get("name", "")),
                                str(p.get("kind", "positional_or_keyword")),
                                p.get("default", False)))
        return cls(component=str(data.get("component") or ""),
                   project=str(data.get("project") or ""),
                   entrypoint=str(data.get("entrypoint") or ""),
                   repo=str(data.get("repo") or ""), commit=str(data.get("commit") or ""),
                   implementation_path=str(data.get("implementation_path") or ""),
                   description_path=str(data.get("description_path") or ""),
                   signature=tuple(params),
                   licence=LicenceClaim(str(licence.get("spdx") or ""),
                                        str(licence.get("source") or "")),
                   checked_on=str(data.get("checked_on") or ""))


def _safe_relative(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(path) and not pure.is_absolute() and ".." not in pure.parts \
        and "\\" not in path


def _module_of(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def bindings_path() -> Path:
    from ..config import registry_dir

    return registry_dir() / "implementation_bindings.yaml"


def parse_bindings(text: str) -> dict[str, ImplementationBinding]:
    """Parse the bindings file: component id -> binding. Duplicates are refused."""
    import yaml

    data = yaml.safe_load(text) or {}
    if not isinstance(data, Mapping):
        raise BindingError("implementation_bindings.yaml must be a mapping")
    unknown = sorted(set(data) - {"api_version", "bindings"})
    if unknown:
        raise BindingError(f"implementation_bindings.yaml has unknown key(s) {unknown}")
    if str(data.get("api_version", "")) != "1":
        raise BindingError("implementation_bindings.yaml must declare api_version \"1\"")
    out: dict[str, ImplementationBinding] = {}
    for entry in data.get("bindings") or ():
        binding = ImplementationBinding.from_dict(entry)
        if binding.component in out:
            raise BindingError(f"component {binding.component!r} is bound twice")
        out[binding.component] = binding
    return out


def load_bindings(path: str | Path | None = None) -> dict[str, ImplementationBinding]:
    """The reviewed bindings. A missing file is no bindings, which binds nothing — every
    catalogue entrypoint then stays a derived candidate the resolver will not run."""
    target = Path(path) if path is not None else bindings_path()
    if not target.is_file():
        return {}
    return parse_bindings(target.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ static checks

def tree_commit(root: str | Path) -> str:
    """The commit a git checkout is at, read from ``.git`` without running git; "" if
    the tree carries no git metadata or it cannot be read."""
    git = Path(root) / ".git"
    try:
        if git.is_file():                      # a worktree or submodule: "gitdir: <path>"
            target = git.read_text(encoding="utf-8").strip().partition("gitdir:")[2]
            git = (Path(root) / target.strip()).resolve()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if _COMMIT.match(head):
        return head
    ref = head.partition("ref:")[2].strip()
    if not ref:
        return ""
    for base in (git, _common_dir(git)):
        try:
            value = (base / ref).read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if _COMMIT.match(value):
            return value
        try:
            packed = (base / "packed-refs").read_text(encoding="utf-8").splitlines()
        except OSError:
            packed = []
        for line in packed:
            sha, _, name = line.partition(" ")
            if name.strip() == ref and _COMMIT.match(sha):
                return sha
    return ""


def _common_dir(git: Path) -> Path:
    try:
        return (git / (git / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        return git


def signature_of(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[Param, ...]:
    """A function's parameters as the binding file states them, read from its AST."""
    a = fn.args
    positional = [*a.posonlyargs, *a.args]
    first_default = len(positional) - len(a.defaults)
    out = [Param(arg.arg, "positional_only" if i < len(a.posonlyargs)
                 else "positional_or_keyword", i >= first_default)
           for i, arg in enumerate(positional)]
    if a.vararg:
        out.append(Param(a.vararg.arg, "var_positional"))
    out += [Param(arg.arg, "keyword_only", d is not None)
            for arg, d in zip(a.kwonlyargs, a.kw_defaults, strict=True)]
    if a.kwarg:
        out.append(Param(a.kwarg.arg, "var_keyword"))
    return tuple(out)


@dataclass(frozen=True)
class ImplementationCheck:
    """What a static look at the implementation file found."""

    ok: bool
    reasons: tuple[str, ...] = ()
    #: third-party modules the function needs: its module's imports and its own
    imports: tuple[str, ...] = ()


_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _module_statements(body: list[ast.stmt]) -> Iterable[ast.stmt]:
    """Statements that run when the module is imported: the body, and the bodies of the
    ``if``/``try``/``with``/``for``/``while``/``match`` blocks in it, but not of a def or a
    class."""
    for node in body:
        yield node
        if isinstance(node, _SCOPE_NODES):
            continue
        for name in ("body", "orelse", "finalbody"):
            yield from _module_statements(getattr(node, name, None) or [])
        for block in [*(getattr(node, "handlers", None) or []),
                      *(getattr(node, "cases", None) or [])]:
            yield from _module_statements(block.body)


def _binds(node: ast.stmt, name: str) -> bool:
    """Whether a module-level statement (re)binds ``name``."""
    if isinstance(node, (*_FUNCTION_NODES, ast.ClassDef)):
        return node.name == name
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any((a.asname or a.name.split(".")[0]) == name for a in node.names)
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        targets = [node.target]
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        targets = [i.optional_vars for i in node.items if i.optional_vars is not None]
    elif isinstance(node, ast.Delete):
        targets = list(node.targets)
    return any(isinstance(n, ast.Name) and n.id == name
               for t in targets for n in ast.walk(t))


def _import_roots(nodes: Iterable[ast.AST]) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def check_implementation(root: str | Path, binding: ImplementationBinding
                         ) -> ImplementationCheck:
    """Statically check that ``binding`` names a module-level function with its signature.

    Refuses, with the reason, when the file is missing or does not parse; when the name is
    not a def at the top of the module body (defined only inside an ``if`` or a ``try``,
    inside a class, or not at all); when a later module-level statement rebinds the name
    (an import, an assignment, a ``del``), so importing the module would not hand back the
    def that was checked; when the def is decorated, because a decorator decides what the
    name finally is and a static check cannot see that; and when the parameters differ
    from the binding's in name, kind, order or whether a default is present.
    """
    root = Path(root)
    path = root / binding.implementation_path
    try:
        inside = path.resolve().is_relative_to(root.resolve())
    except OSError:
        inside = False
    if not inside or not path.is_file():
        return ImplementationCheck(False, (f"implementation file {binding.implementation_path}"
                                           " does not exist in the tree",))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        return ImplementationCheck(False, (f"{binding.implementation_path} does not parse: "
                                           f"{type(exc).__name__}: {exc}",))
    name = binding.function
    defs = [n for n in tree.body if isinstance(n, _FUNCTION_NODES) and n.name == name]
    if not defs:
        nested = [n for n in ast.walk(tree) if isinstance(n, _FUNCTION_NODES)
                  and n.name == name]
        where = (f"; it is defined at line {nested[0].lineno}, inside another block"
                 if nested else "")
        return ImplementationCheck(False, (f"{name} is not defined at module level in "
                                           f"{binding.implementation_path}{where}",))
    fn = defs[-1]
    later = [n for n in _module_statements(tree.body)
             if n is not fn and n.lineno > fn.lineno and _binds(n, name)]
    if later:
        return ImplementationCheck(False, (
            f"{name} is rebound at line {later[0].lineno} of "
            f"{binding.implementation_path}, after the def at line {fn.lineno}",))
    if fn.decorator_list:
        return ImplementationCheck(False, (
            f"{name} is decorated (line {fn.lineno}); the decorator decides what the name "
            "is bound to, which a static check cannot see",))
    found = signature_of(fn)
    if found != binding.signature:
        return ImplementationCheck(False, (
            f"{name} takes ({render_signature(found)}) and the binding expects "
            f"({render_signature(binding.signature)})",))
    module_level = [n for n in _module_statements(tree.body)
                    if isinstance(n, (ast.Import, ast.ImportFrom))]
    roots = _import_roots(module_level) | _import_roots(ast.walk(fn))
    roots.add(binding.module.split(".")[0])
    std = set(sys.stdlib_module_names)
    imports = tuple(sorted(m for m in roots if m not in std and not m.startswith("_")))
    return ImplementationCheck(True, (), imports=imports)
