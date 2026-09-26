"""One governed entry for running a P0 skill: manifest, lock, audit chain, verified release.

Before this module the CLI imported the skill function and called it directly. The
``--dir`` it accepted was never read, the manifest and lockfile were not consulted,
nothing was recorded in PSH's audit chain, and the verdict it printed was
``publishable`` — a statement about the artifact's shape, not about whether its
quotes, its outputs or its execution had been checked (audit F03, F06).

:func:`run_governed` is the one path from "run this skill" to "this artifact may be
released", and every step is a refusal point:

1. **Manifest.** The skill is loaded from the directory the caller names. A missing
   directory, or one without this skill, is refused — not silently replaced by the
   built-in table.
2. **Entrypoint.** The manifest's ``runtime.entrypoint`` must be the callable that
   will run. A manifest that names one function while another executes is the
   drift a pin exists to prevent.
3. **Lock.** When a lockfile is given (or found beside the tree), the skill's
   content hash — manifest plus its code's import closure — must match the pin.
4. **Audit.** The run executes under a PSH ``TrustedKernel``: a start and a
   validation event are appended to its hash-chained event store, and the
   artifact is stamped with the policy id and the chain head. A kernel that cannot
   record the run refuses it.
5. **Verification.** The artifact is validated with the content store its quotes
   and outputs were registered in, and with the directory its outputs were written
   to, so ``evidence_verified`` and ``outputs_verified`` are checked facts.

The result carries the attested artifact and the full verdict; ``released`` is
``verdict.release_authorized`` and nothing else.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import tempfile
import typing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .contracts import ResearchArtifact, validate_artifact
from .contracts.artifact import ArtifactVerdict
from .contracts.receipts import ContentStore, default_store

__all__ = ["GovernedRun", "GovernedRunRefused", "coerce_arguments", "run_governed",
           "skill_callables"]


class GovernedRunRefused(RuntimeError):
    """The run could not be admitted, executed under audit, or verified."""


@dataclass(frozen=True)
class GovernedRun:
    artifact: ResearchArtifact
    verdict: ArtifactVerdict
    skill_id: str
    content_hash: str
    audit_head: str
    state_dir: str
    output_dir: str
    written: tuple[str, ...] = ()

    @property
    def released(self) -> bool:
        return self.verdict.release_authorized


def skill_callables() -> dict[str, Callable[..., ResearchArtifact]]:
    """The P0 skills, by id.

    Built here rather than discovered by introspection so that adding a skill is a
    deliberate edit. The manifest is the authority on what a skill *is*; this table
    is where implementation and manifest are joined, and :func:`run_governed`
    cross-checks the two on every run.
    """
    from .skills.p0 import (analyze_tcm_network_pharmacology, assess_tcm_safety,
                            normalize_tcm_entities, retrieve_tcm_evidence)

    return {
        "normalize-tcm-entities": normalize_tcm_entities,
        "retrieve-tcm-evidence": retrieve_tcm_evidence,
        "analyze-tcm-network-pharmacology": analyze_tcm_network_pharmacology,
        "assess-tcm-safety": assess_tcm_safety,
    }


# ---------------------------------------------------------------------------
# argument coercion
# ---------------------------------------------------------------------------

_LIST_SEPARATORS = (",", "，", "、", ";", "；")


def _is_sequence(annotation: Any) -> bool:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or str(origin) == "types.UnionType":
        return any(_is_sequence(a) for a in typing.get_args(annotation)
                   if a is not type(None))
    target = origin or annotation
    if target in (str, bytes):
        return False
    try:
        import collections.abc as cabc
        return isinstance(target, type) and issubclass(
            target, (list, tuple, set, frozenset, cabc.Sequence, cabc.Iterable))
    except TypeError:
        return False


def _scalar_type(annotation: Any, default: Any) -> type | None:
    for candidate in (annotation, type(default) if default is not inspect.Parameter.empty
                      and default is not None else None):
        if candidate in (bool, int, float, str):
            return candidate
    return None


def coerce_arguments(fn: Callable[..., Any], raw: Mapping[str, str]) -> dict[str, Any]:
    """Turn ``name=value`` strings into the types ``fn`` declares.

    A parameter annotated as a sequence always receives a list — one name gives a
    one-element list. An earlier version returned a bare string whenever the value
    held no comma, and the skill iterated it character by character: ``names=黄芪``
    became the two queries ``黄`` and ``芪`` (audit F07). Scalars follow the
    annotation or the default's type; an unknown name is refused, not ignored.
    """
    try:
        hints = typing.get_type_hints(fn)
    except Exception:                                        # noqa: BLE001
        hints = {}
    params = inspect.signature(fn).parameters
    out: dict[str, Any] = {}
    for name, value in raw.items():
        param = params.get(name)
        if param is None or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise GovernedRunRefused(
                f"unknown argument {name!r}; parameters: {', '.join(params)}")
        annotation = hints.get(name, param.annotation)
        if _is_sequence(annotation):
            text = value
            for sep in _LIST_SEPARATORS[1:]:
                text = text.replace(sep, ",")
            out[name] = [v.strip() for v in text.split(",") if v.strip()]
            continue
        kind = _scalar_type(annotation, param.default)
        try:
            if kind is bool:
                lowered = value.strip().lower()
                if lowered not in ("1", "0", "true", "false", "yes", "no"):
                    raise ValueError(value)
                out[name] = lowered in ("1", "true", "yes")
            elif kind in (int, float):
                out[name] = kind(value.strip())
            else:
                out[name] = value.strip()
        except ValueError:
            raise GovernedRunRefused(
                f"argument {name!r} expects {kind.__name__}, got {value!r}") from None
    return out


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def _find_lockfile(skill_dir: Path) -> Path | None:
    for parent in (skill_dir, *skill_dir.parents):
        candidate = parent / "registry" / "skills.lock.yaml"
        if candidate.is_file():
            return candidate
    return None


def _load(skill_id: str, skill_dir: Path) -> Any:
    from .skills.loader import load_skills

    if not skill_dir.is_dir():
        raise GovernedRunRefused(f"skill directory {skill_dir} does not exist")
    loaded, refused = load_skills(skill_dir)
    for skill in loaded:
        if skill.spec.id == skill_id:
            return skill
    why = next((w for d, w in refused if d.name == skill_id), "")
    raise GovernedRunRefused(
        f"skill {skill_id!r} has no loadable manifest under {skill_dir}"
        + (f": {why}" if why else ""))


def run_governed(skill_id: str, arguments: Mapping[str, Any], *,
                 skill_dir: str | Path, state_dir: str | Path | None = None,
                 output_dir: str | Path | None = None,
                 lockfile: str | Path | None = None,
                 content_store: ContentStore | None = None,
                 callables: Mapping[str, Callable[..., ResearchArtifact]] | None = None,
                 ) -> GovernedRun:
    """Run ``skill_id`` through manifest, lock, audit and verification.

    ``arguments`` are passed to the skill as given; use :func:`coerce_arguments` to
    build them from strings. ``state_dir`` holds the PSH kernel's audit chain (a
    temporary directory when omitted — attested, but not durable), ``output_dir``
    receives the declared outputs (a temporary directory when omitted).
    """
    try:
        from psh import PSHConfig, TrustedKernel
    except ImportError as exc:
        raise GovernedRunRefused(
            f"PSH is not importable ({exc}); a governed run needs its audit chain") from exc

    from .skills.base import write_outputs
    from .skills.loader import skill_content_hash

    table = dict(callables) if callables is not None else skill_callables()
    fn = table.get(skill_id)
    if fn is None:
        raise GovernedRunRefused(
            f"unknown skill {skill_id!r}; available: {', '.join(sorted(table))}")
    skill_dir = Path(skill_dir).resolve()
    skill = _load(skill_id, skill_dir)
    entrypoint = skill.spec.runtime.entrypoint
    declared = f"{fn.__module__}:{fn.__qualname__}"
    if entrypoint != declared:
        raise GovernedRunRefused(
            f"manifest entrypoint {entrypoint!r} is not the callable that would run "
            f"({declared!r})")
    content_hash = skill_content_hash(skill.directory, entrypoint=entrypoint)

    lock = Path(lockfile) if lockfile else _find_lockfile(skill_dir)
    if lock is not None:
        from .updates import load_lockfile
        pins = {v.skill_id: v for v in load_lockfile(lock.read_text(encoding="utf-8"))}
        pin = pins.get(skill_id)
        if pin is None:
            raise GovernedRunRefused(f"skill {skill_id!r} is not pinned in {lock}")
        if pin.content_hash != content_hash:
            raise GovernedRunRefused(
                f"skill {skill_id!r} hashes {content_hash[:12]} but {lock.name} pins "
                f"{pin.content_hash[:12]}; the code that would run is not the code that "
                "was reviewed")

    store = content_store if content_store is not None else default_store
    state = Path(state_dir) if state_dir else Path(tempfile.mkdtemp(prefix="bioagent-psh-"))
    out = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="bioagent-out-"))
    args_digest = hashlib.sha256(json.dumps(
        dict(arguments), sort_keys=True, ensure_ascii=False, default=str
    ).encode("utf-8")).hexdigest()

    kernel = TrustedKernel(PSHConfig(state_dir=state).ensure_dirs())
    try:
        try:
            kernel.audit("bioscience_skill_run_started", run_id=skill_id,
                         detail={"skill_id": skill_id, "version": skill.spec.version,
                                 "content_hash": content_hash,
                                 "arguments_sha256": args_digest,
                                 "lockfile": str(lock) if lock else ""})
        except Exception as exc:                             # noqa: BLE001
            raise GovernedRunRefused(f"the audit chain refused the run: {exc}") from exc

        artifact = fn(**dict(arguments))
        if not isinstance(artifact, ResearchArtifact):
            raise GovernedRunRefused(f"{skill_id} returned {type(artifact).__name__}, "
                                     "not a ResearchArtifact")
        try:
            written = tuple(str(p) for p in write_outputs(artifact, out, store=store))
        except ValueError as exc:
            raise GovernedRunRefused(f"outputs could not be materialised: {exc}") from exc

        before = validate_artifact(artifact, output_root=out, content_store=store)
        kernel.audit("bioscience_artifact_validated", run_id=skill_id,
                     detail={"artifact_digest": artifact.digest,
                             "states": {k: v for k, v in before.states.items()
                                        if k != "execution_attested"},
                             "codes": list(before.codes)})
        chain = kernel.events.verify()
        if not chain:
            raise GovernedRunRefused("the audit chain does not verify after the run")
        head = kernel.events.head_hash
        attested = replace(artifact, policy_id=kernel.policy.profile_id or "default",
                           audit_head=head,
                           provenance={**dict(artifact.provenance),
                                       "governed": {"skill_content_hash": content_hash,
                                                    "arguments_sha256": args_digest,
                                                    "pre_attestation_digest": artifact.digest,
                                                    "state_dir": str(state)}})
        verdict = validate_artifact(attested, output_root=out, content_store=store)
    finally:
        kernel.close()
    return GovernedRun(artifact=attested, verdict=verdict, skill_id=skill_id,
                       content_hash=content_hash, audit_head=head, state_dir=str(state),
                       output_dir=str(out), written=written)
