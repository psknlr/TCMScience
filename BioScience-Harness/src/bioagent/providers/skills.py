"""Provider for SKILL.md-based skills: upstream ecosystems and this project's own.

Upstream projects (K-Dense, ClawBio, PantheonOS) ship skills as directories with YAML
frontmatter, so discovery is a filesystem walk rather than an import. Skills carry no
python entrypoint, so they are declared with the `none` backend and reach READY only
when a host agent executes them — which the manifest states rather than implies.

A skill of this project may also carry a ``skill.yaml`` next to its ``SKILL.md``: a
machine-readable contract (``SkillContract``) naming its inputs, the source snapshots and
tools it needs, and the strongest kind of claim it may produce. The contract is validated
at discovery; a skill whose contract is invalid is catalogued as UNAVAILABLE with the
reason. The contract can only *ask*: what a run actually gets is the request intersected
with the enabled source cards and the run's allowance (``SkillContract.grant``), because
a skill's directory is agent-writable and its own file must not be its whitelist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from ..runtime.component import (ComponentManifest, LicenseSpec, Permissions,
                                 Provider as ProvBlock, Requirements, RuntimeSpec,
                                 Validation)
from .base import Provider

_FM = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def parse_frontmatter(text: str) -> dict:
    """Minimal YAML-frontmatter reader (flat keys plus one nested level)."""
    m = _FM.match(text)
    if not m:
        return {}
    out: dict = {}
    stack: list[tuple[int, dict]] = [(0, out)]
    for raw in m.group(1).split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        while stack and indent < stack[-1][0]:
            stack.pop()
        target = stack[-1][1] if stack else out
        if line.startswith("- "):
            key = getattr(parse_frontmatter, "_last_key", None)
            if key:
                target.setdefault(key, [])
                if isinstance(target[key], list):
                    target[key].append(line[2:].strip().strip('"\''))
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip(); v = v.strip().strip('"\'')
            parse_frontmatter._last_key = k
            if not v:
                child: dict = {}
                target[k] = child
                stack.append((indent + 2, child))
            else:
                target[k] = v
    return out


class SkillContractError(ValueError):
    """A ``skill.yaml`` that does not state a usable contract."""


_SKILL_ID = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)+$")


@dataclass(frozen=True)
class SkillContract:
    """What a skill needs and the most it may conclude. Block-style YAML::

        id: tcm.network-pharmacology
        version: 0.1.0
        inputs:
          formula: FormulaRef
          indication: DiseaseOrSyndromeRef
        requires:
          sources:
            - npass@2.0
            - lotus@2026-04-13
          tools:
            - sources.composition
            - stats.enrichment_analysis
        steps:
          composition:
            tool: sources.composition
          enrichment:
            tool: stats.enrichment_analysis
            after:
              - composition
            design: in_silico
        max_claim_kind: mechanism_hypothesis

    ``steps`` is optional. Each step runs one of ``requires.tools`` after the steps it
    names; a step whose output is evidence states its study design (one of PSH's
    ``DESIGNS``), which is what the PSH compiler checks a claim against
    (``bioagent.psh.skill_program``).
    """

    id: str
    version: str
    inputs: Mapping[str, str] = field(default_factory=dict)
    sources: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    max_claim_kind: str = "mechanism_hypothesis"
    #: step id -> {"tool": str, "after": tuple[str, ...], "design": str}
    steps: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from ..sources.cards import parse_ref
        from ..tcm.model import CLAIM_SUPPORT

        if not _SKILL_ID.match(self.id):
            raise SkillContractError(f"skill id {self.id!r} must be dotted lowercase, "
                                     "e.g. tcm.network-pharmacology")
        if not self.version:
            raise SkillContractError(f"skill {self.id!r} has no version")
        if self.max_claim_kind not in CLAIM_SUPPORT:
            raise SkillContractError(f"skill {self.id!r}: max_claim_kind "
                                     f"{self.max_claim_kind!r} is not one of "
                                     f"{sorted(CLAIM_SUPPORT)}")
        for ref in self.sources:
            try:
                parse_ref(ref)
            except ValueError as exc:
                raise SkillContractError(f"skill {self.id!r}: {exc}") from None
        from ..sources.schema import STUDY_DESIGNS
        designs = set(STUDY_DESIGNS) - {"chemical_analysis", "evidence_aggregate"}
        for step, spec in self.steps.items():
            where = f"skill {self.id!r} step {step!r}"
            if set(spec) - {"tool", "after", "design"}:
                raise SkillContractError(f"{where}: unknown fields "
                                         f"{sorted(set(spec) - {'tool', 'after', 'design'})}")
            if spec.get("tool") not in self.tools:
                raise SkillContractError(f"{where}: tool {spec.get('tool')!r} is not in "
                                         "requires.tools")
            for dep in spec.get("after") or ():
                if dep not in self.steps or dep == step:
                    raise SkillContractError(f"{where}: runs after unknown step {dep!r}")
            if spec.get("design") and spec["design"] not in designs:
                raise SkillContractError(f"{where}: design {spec['design']!r} is not one of "
                                         f"{sorted(designs)}")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SkillContract":
        unknown = set(data) - {"id", "version", "inputs", "requires", "max_claim_kind",
                               "steps"}
        if unknown:
            raise SkillContractError(f"unknown skill.yaml fields: {sorted(unknown)}")
        requires = data.get("requires") or {}
        if not isinstance(requires, Mapping):
            raise SkillContractError("requires must be a mapping with sources and tools")
        extra = set(requires) - {"sources", "tools"}
        if extra:
            raise SkillContractError(f"unknown requires fields: {sorted(extra)}")
        inputs = data.get("inputs") or {}
        if not isinstance(inputs, Mapping):
            raise SkillContractError("inputs must map each input name to its type")
        return cls(id=str(data.get("id") or ""), version=str(data.get("version") or ""),
                   inputs={str(k): str(v) for k, v in inputs.items()},
                   sources=_strings(requires.get("sources"), "requires.sources"),
                   tools=_strings(requires.get("tools"), "requires.tools"),
                   max_claim_kind=str(data.get("max_claim_kind") or "mechanism_hypothesis"),
                   steps=_steps(data.get("steps")))

    @classmethod
    def load(cls, path: str | Path) -> "SkillContract":
        from ..runtime.component import ComponentManifest

        text = Path(path).read_text(encoding="utf-8")
        return cls.from_mapping(ComponentManifest._parse_yaml_subset(text))

    def permits(self, claim_kind: str) -> bool:
        """Whether this skill may emit a claim of ``claim_kind``.

        A kind is no stronger than the ceiling when every tier that licenses the ceiling
        also licenses it. Capped at ``mechanism_hypothesis`` a skill may not emit
        ``mechanism`` (which drops the computational tier); capped at ``efficacy`` it may
        emit ``association`` and ``safety_signal`` but not ``recommendation``.
        """
        from ..tcm.model import CLAIM_SUPPORT

        if claim_kind not in CLAIM_SUPPORT:
            return False
        return CLAIM_SUPPORT[claim_kind] >= CLAIM_SUPPORT[self.max_claim_kind]

    def grant(self, *, allowed: Iterable[str] | None = None,
              cards: Iterable[Any] | None = None) -> tuple[dict[str, str], dict[str, str]]:
        """The sources a run of this skill gets: request ∩ enabled cards ∩ allowance."""
        from ..sources.cards import SOURCE_CARDS, effective_sources

        return effective_sources(self.sources, cards=SOURCE_CARDS if cards is None else cards,
                                 allowed=allowed)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "inputs": dict(self.inputs),
                "requires": {"sources": list(self.sources), "tools": list(self.tools)},
                "max_claim_kind": self.max_claim_kind,
                **({"steps": {k: dict(v) for k, v in self.steps.items()}} if self.steps else {})}


def _steps(value: Any) -> dict[str, dict[str, Any]]:
    if value in (None, "", {}):
        return {}
    if not isinstance(value, Mapping):
        raise SkillContractError("steps must map each step id to its tool, after and design")
    out = {}
    for step, spec in value.items():
        if not isinstance(spec, Mapping):
            raise SkillContractError(f"step {step!r} must be a mapping")
        out[str(step)] = {**{k: v for k, v in spec.items() if k != "after"},
                          "after": _strings(spec.get("after"), f"steps.{step}.after")}
        if "tool" in spec:
            out[str(step)]["tool"] = str(spec["tool"])
    return out


def _strings(value: Any, what: str) -> tuple[str, ...]:
    if value in (None, "", []):
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise SkillContractError(f"{what} must be a list")
    return tuple(str(v) for v in value)


class SkillDirectoryProvider(Provider):
    """Discovers skills from a repo containing SKILL.md files."""

    def __init__(self, name: str, root: Path | str, license_spdx: str = "MIT",
                 commit: str = "") -> None:
        self.name = name
        self.root = Path(root)
        self.license_spdx = license_spdx
        self.commit = commit

    def available(self) -> bool:
        return self.root.is_dir()

    def discover(self) -> Iterator[ComponentManifest]:
        if not self.available():
            return
        for p in sorted(self.root.rglob("SKILL.md")):
            text = p.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(text)
            nm = str(fm.get("name") or p.parent.name)
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
            rel = str(p.relative_to(self.root))
            cid = f"{self.name.lower()}.skill.{re.sub(r'[^a-z0-9_.-]+','-',nm.lower())}"
            contract, problem = self._contract(p.parent)
            manifest = ComponentManifest(
                id=cid, kind="skill", name=nm,
                version=str(meta.get("version") or "0.1.0"),
                description=" ".join(str(fm.get("description", "")).split())[:400],
                domain=(str(tags[0]) if tags else "general"),
                provider=ProvBlock(project=self.name, commit=self.commit, source_path=rel),
                # A skill is a specification for a host agent, not a callable.
                runtime=RuntimeSpec(backend="none"),
                inputs={"skill_contract": contract.as_dict()} if contract else {},
                requires=Requirements(
                    services=tuple(ref.split("@", 1)[0] for ref in contract.sources)
                    if contract else (),
                    components=contract.tools if contract else ()),
                permissions=Permissions(),
                license=LicenseSpec(spdx=str(fm.get("license") or self.license_spdx),
                                    integration_mode="vendor"),
                validation=Validation(smoke_test=str(meta.get("demo_data") or "")),
                offline_capable=False,
            )
            if problem:
                manifest.mark_unavailable(f"invalid skill.yaml: {problem}")
            yield manifest

    @staticmethod
    def _contract(directory: Path) -> tuple[SkillContract | None, str]:
        path = directory / "skill.yaml"
        if not path.is_file():
            return None, ""
        if re.search(r"(?m)^api_version\s*:", path.read_text(encoding="utf-8",
                                                             errors="replace")):
            # a registry manifest (bioagent.skills), compiled by its own loader
            return None, ""
        try:
            return SkillContract.load(path), ""
        except (SkillContractError, ValueError) as exc:
            return None, str(exc)
