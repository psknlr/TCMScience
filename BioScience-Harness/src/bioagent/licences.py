"""Licence records for what a run uses — code, models, data, services — and the gate that
a commercial purpose must pass.

Two rules existed and nothing joined them. ``psh.licensing`` rules on a code licence and
the way the code is integrated, and lets an unlicensed component run ``federated`` because
invoking it is not redistributing it. ``sources.cards.effective_sources`` rules on a data
source for a run's purpose. Neither knows model weights, whose terms often bind what may be
done with the outputs, or API services, whose terms of use and rate limits are a contract
with whoever holds the account — and the code matrix's ``NONE + federated`` was being read
as permission to use upstream code in a product, which it never was.

A record states the terms of one asset and where they were read:

* ``CodeLicence``  — SPDX id, repository, commit, the licence file and its digest, and the
  integration mode the use was reviewed in;
* ``ModelLicence`` — weights version and digest, the licence, the purposes its terms
  permit, and what they say about outputs;
* ``DataLicence``  — version, the licence of the records (and per record where it
  differs), whether commercial use is allowed, retention and redistribution;
* ``ServiceTerms`` — terms reference and version, the account a run uses (a reference,
  never a secret), the purposes permitted, and the rate limit.

``usage_decision`` is the gate. For purpose ``commercial`` every asset a run actually uses
needs a record that explicitly permits that use: code is ruled by ``psh.licensing`` with
``commercial=True``, a source card by ``effective_sources`` itself, any other data by the
same rule, models and services by the purposes their terms name. An asset with no record
is refused, never assumed: the catalogue's licence column is provenance of a row, and for
an aggregator's row it is the aggregator's licence (``LicenseSpec.catalogue``). For purpose
``academic`` no licence record refuses anything: the policy kernel and the source cards
rule on research use exactly as they did, and the runtime does not consult the gate.

``UsageDecision.as_dict()`` is what a run's provenance carries — each asset, the record and
terms relied on, each verdict and the overall one — and ``UsageDecision.authorization``
folds the rulings into the call's ``Authorization``, so a refusal and its reason sit where
every other policy refusal does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar, Iterable, Mapping, Union

from .policy import Authorization, PolicyDecision, Ruling
from .sources.cards import PURPOSES, SOURCE_CARDS, SourceCard, effective_sources, parse_ref

__all__ = ["ASSET_KINDS", "COMMERCIAL_USE", "OUTPUT_TERMS", "Asset", "Checked",
           "CodeLicence", "DataLicence", "LicenceRecordError", "LicenceRecords",
           "ModelLicence", "ServiceTerms", "UsageDecision", "UsageEntry", "assets_for",
           "licence_records_path", "usage_decision"]

ASSET_KINDS: tuple[str, ...] = ("code", "model", "data", "service")
#: As on a source card: ``unknown`` is not ``allowed``.
COMMERCIAL_USE: tuple[str, ...] = ("allowed", "forbidden", "unknown")
#: What a model's terms say about its outputs. Only the first two let a product use them.
OUTPUT_TERMS: tuple[str, ...] = ("unrestricted", "attribution", "non_commercial", "unknown")
_COMMERCIAL_OUTPUTS = frozenset({"unrestricted", "attribution"})

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LicenceRecordError(ValueError):
    """A record that would let an asset through without the facts that make its use lawful."""


@dataclass(frozen=True)
class Checked:
    """Where a record's facts were read (a file at a commit, a terms page and its version),
    and on which day. The YAML key is ``date``: PyYAML reads a bare ``on`` as ``True``."""

    source: str
    date: str

    def __post_init__(self) -> None:
        if not self.source.strip() or not _ISO.match(self.date):
            raise LicenceRecordError("a record says where its terms were read, and when "
                                     "(an ISO date)")

    def as_dict(self) -> dict[str, str]:
        return {"source": self.source, "date": self.date}


def _problems(record: Any) -> list[str]:
    """What every record must state, whatever it is a record of."""
    out = []
    if not re.match(rf"^{record.kind}\.[a-z0-9][a-z0-9._-]*$", record.id):
        out.append(f"id {record.id!r} must read {record.kind}.<name>")
    if not record.name.strip():
        out.append("no name")
    if any(not c or " " in c for c in record.components):
        out.append("a component id is empty or has a space")
    return out


def _raise(record: Any, problems: list[str]) -> None:
    if problems:
        raise LicenceRecordError(f"{record.kind} record {record.id!r}: " + "; ".join(problems))


def _purposes(values: Iterable[str]) -> list[str]:
    bad = sorted(set(values) - set(PURPOSES))
    return [f"permitted_use names {bad}; purposes are {list(PURPOSES)}"] if bad else []


@dataclass(frozen=True)
class CodeLicence:
    """A code licence read from the licence file of a repository at a commit."""

    kind: ClassVar[str] = "code"
    id: str
    name: str
    spdx: str
    licence_path: str
    licence_sha256: str
    integration_mode: str
    checked: Checked
    repo: str = ""
    commit: str = ""
    #: The run's own code, read from this repository: no upstream repository or commit.
    first_party: bool = False
    #: Catalogue component ids whose implementation this is.
    components: tuple[str, ...] = ()
    #: Top-level import names, for code a component requires or an entrypoint imports.
    modules: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        problems = _problems(self)
        if not self.spdx.strip():
            problems.append("no SPDX id")
        if self.integration_mode not in ("vendor", "native", "federated"):
            problems.append(f"integration_mode {self.integration_mode!r}")
        if not self.licence_path and not self.licence_sha256:
            # A repository with no licence file at the commit: the record states the
            # absence, which grants nothing, and only as NONE.
            if self.spdx != "NONE":
                problems.append("without a licence file the only honest SPDX value is NONE")
        elif not self.licence_path.strip() or not _SHA256.match(self.licence_sha256):
            problems.append("the licence file and the sha256 of its text are both required")
        if self.first_party and (self.repo or self.commit):
            problems.append("first-party code is this repository; it names no upstream")
        if not self.first_party and not (self.repo.startswith("https://")
                                          and _SHA1.match(self.commit)):
            problems.append("an upstream record names its https repository and the full "
                            "commit the licence was read at")
        if not self.components and not self.modules:
            problems.append("covers no component and no module")
        _raise(self, problems)

    @property
    def licence_url(self) -> str:
        """The licence file at the pinned commit, where anyone can read what was read."""
        if self.first_party or not self.licence_path:
            return ""
        return f"{self.repo.rstrip('/')}/blob/{self.commit}/{self.licence_path}"

    def terms(self) -> dict[str, Any]:
        return {"spdx": self.spdx, "repo": self.repo or "this repository",
                "commit": self.commit, "licence_path": self.licence_path,
                "licence_url": self.licence_url, "licence_sha256": self.licence_sha256,
                "integration_mode": self.integration_mode,
                "checked": self.checked.as_dict()}

    def commercial_ruling(self, asset: "Asset") -> Ruling:
        """``psh.licensing`` on this licence and how the asset is used, for a commercial
        purpose. Without PSH the use is refused, not ruled on by a weaker table."""
        try:
            from psh.licensing import LicenseDecision, license_ruling
        except ImportError as exc:
            return Ruling(PolicyDecision.DENY,
                          f"{self.name}: psh.licensing is not importable ({exc}), so a "
                          "commercial use of this code cannot be ruled on and is refused",
                          "usage.code.no_psh")
        ruling = license_ruling(self.spdx, asset.mode or self.integration_mode,
                                commercial=True)
        decision = {LicenseDecision.ALLOW: PolicyDecision.ALLOW,
                    LicenseDecision.PREFER_ALTERNATIVE: PolicyDecision.PREFER_ALTERNATIVE,
                    }.get(ruling.decision, PolicyDecision.DENY)
        return Ruling(decision, f"{self.name} ({self.spdx}, {ruling.mode}): {ruling.reason}",
                      f"usage.code.{ruling.rule}")


@dataclass(frozen=True)
class ModelLicence:
    """The terms of a set of model weights, and of what is done with their outputs."""

    kind: ClassVar[str] = "model"
    id: str
    name: str
    weights_version: str
    weights_sha256: str
    licence: str
    terms_url: str
    permitted_use: tuple[str, ...]
    output_terms: str
    checked: Checked
    components: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        problems = _problems(self) + _purposes(self.permitted_use)
        if not self.weights_version.strip() or not _SHA256.match(self.weights_sha256):
            problems.append("the weights version and the sha256 of the weights are required")
        if not self.licence.strip() or not self.terms_url.startswith("https://"):
            problems.append("the licence and an https reference to its terms are required")
        if self.output_terms not in OUTPUT_TERMS:
            problems.append(f"output_terms {self.output_terms!r} is not one of {OUTPUT_TERMS}")
        _raise(self, problems)

    def terms(self) -> dict[str, Any]:
        return {"licence": self.licence, "terms_url": self.terms_url,
                "weights_version": self.weights_version,
                "weights_sha256": self.weights_sha256,
                "permitted_use": list(self.permitted_use), "output_terms": self.output_terms,
                "checked": self.checked.as_dict()}

    def commercial_ruling(self, asset: "Asset") -> Ruling:
        if "commercial" not in self.permitted_use:
            return Ruling(PolicyDecision.DENY,
                          f"{self.name}: its terms ({self.licence}) permit "
                          f"{list(self.permitted_use)}, not commercial use",
                          "usage.model.purpose")
        if self.output_terms not in _COMMERCIAL_OUTPUTS:
            return Ruling(PolicyDecision.DENY,
                          f"{self.name}: its terms say {self.output_terms} about outputs, "
                          "so a product may not use what the model produces",
                          "usage.model.outputs")
        return Ruling(PolicyDecision.ALLOW,
                      f"{self.name}: its terms ({self.licence}) permit commercial use, "
                      f"outputs {self.output_terms}", "usage.model.allowed")


@dataclass(frozen=True)
class DataLicence:
    """The terms of a dataset's records, overall and where a record says otherwise."""

    kind: ClassVar[str] = "data"
    id: str
    name: str
    version: str
    licence: str
    commercial_use: str
    retention: str
    redistribution: str
    checked: Checked
    #: field -> {field value -> licence}, as ``SourceCard.per_record_license``.
    per_record: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    components: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        problems = _problems(self)
        if not self.licence.strip() or not self.version.strip():
            problems.append("the licence and the version it applies to are required")
        if self.commercial_use not in COMMERCIAL_USE:
            problems.append(f"commercial_use {self.commercial_use!r} is not one of "
                            f"{COMMERCIAL_USE}")
        if not self.retention.strip() or not self.redistribution.strip():
            problems.append("retention and redistribution are stated, if only as 'unknown'")
        _raise(self, problems)

    def terms(self) -> dict[str, Any]:
        return {"licence": self.licence, "version": self.version,
                "per_record": {k: dict(v) for k, v in self.per_record.items()},
                "commercial_use": self.commercial_use, "retention": self.retention,
                "redistribution": self.redistribution,
                "checked": self.checked.as_dict()}

    def commercial_ruling(self, asset: "Asset") -> Ruling:
        """The rule ``effective_sources`` applies to a card, applied to this record."""
        if self.commercial_use != "allowed":
            return Ruling(PolicyDecision.DENY,
                          f"a commercial run needs commercial use allowed; {self.name}'s "
                          f"terms ({self.licence}) say {self.commercial_use}",
                          "usage.data.commercial_use")
        return Ruling(PolicyDecision.ALLOW,
                      f"{self.name} ({self.licence}) allows commercial use",
                      "usage.data.allowed")


@dataclass(frozen=True)
class ServiceTerms:
    """The terms of use of an API service, for the account a run uses."""

    kind: ClassVar[str] = "service"
    id: str
    name: str
    terms_url: str
    terms_version: str
    #: Where the account's credential lives (``env:NCBI_API_KEY``), or ``none``. Never the
    #: credential itself: this file is reviewed in the open and shipped in the wheel.
    account: str
    permitted_use: tuple[str, ...]
    rate_limit: str
    checked: Checked
    components: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        problems = _problems(self) + _purposes(self.permitted_use)
        if not self.terms_url.startswith("https://") or not self.terms_version.strip():
            problems.append("an https reference to the terms and their version are required")
        if not (self.account == "none" or re.match(r"^(env|vault):[A-Za-z0-9_./-]+$",
                                                   self.account)):
            problems.append("account is 'none' or a reference such as env:NAME, never a "
                            "credential")
        if not self.rate_limit.strip():
            problems.append("the rate limit is stated, if only as 'unknown'")
        _raise(self, problems)

    def terms(self) -> dict[str, Any]:
        return {"terms_url": self.terms_url, "terms_version": self.terms_version,
                "account": self.account, "permitted_use": list(self.permitted_use),
                "rate_limit": self.rate_limit,
                "checked": self.checked.as_dict()}

    def commercial_ruling(self, asset: "Asset") -> Ruling:
        if "commercial" not in self.permitted_use:
            return Ruling(PolicyDecision.DENY,
                          f"{self.name}: its terms ({self.terms_version}) permit "
                          f"{list(self.permitted_use)}, not commercial use",
                          "usage.service.purpose")
        return Ruling(PolicyDecision.ALLOW,
                      f"{self.name}: its terms ({self.terms_version}) permit commercial use "
                      f"at {self.rate_limit}", "usage.service.allowed")


Record = Union[CodeLicence, ModelLicence, DataLicence, ServiceTerms]
_RECORD_TYPES: Mapping[str, type] = {"code": CodeLicence, "model": ModelLicence,
                                     "data": DataLicence, "service": ServiceTerms}


# ----------------------------------------------------------------- the registry

def licence_records_path() -> Path:
    from .config import registry_dir

    return registry_dir() / "licence_records.yaml"


class LicenceRecords:
    """The reviewed records, indexed for the questions the gate and the catalogue ask."""

    def __init__(self, code: Iterable[CodeLicence] = (), models: Iterable[ModelLicence] = (),
                 data: Iterable[DataLicence] = (), services: Iterable[ServiceTerms] = (), *,
                 cards: Iterable[SourceCard] = SOURCE_CARDS) -> None:
        self.records: tuple[Record, ...] = (*code, *models, *data, *services)
        self.cards = tuple(cards)
        ids = [r.id for r in self.records]
        if len(ids) != len(set(ids)):
            raise LicenceRecordError(f"duplicate record ids: "
                                     f"{sorted({i for i in ids if ids.count(i) > 1})}")
        self._by_component: dict[tuple[str, str], Record] = {}
        self._by_module: dict[str, CodeLicence] = {}
        for r in self.records:
            for cid in r.components:
                if (r.kind, cid) in self._by_component:
                    raise LicenceRecordError(f"{r.kind} component {cid!r} has two records")
                self._by_component[(r.kind, cid)] = r
            for module in getattr(r, "modules", ()):
                if module in self._by_module:
                    raise LicenceRecordError(f"module {module!r} has two records")
                self._by_module[module] = r

    def get(self, record_id: str) -> Record | None:
        return next((r for r in self.records if r.id == record_id), None)

    def code_for_component(self, component_id: str) -> CodeLicence | None:
        found = self._by_component.get(("code", component_id))
        return found if isinstance(found, CodeLicence) else None

    def attached_to(self, component_id: str) -> tuple[Record, ...]:
        return tuple(r for r in self.records if component_id in r.components)

    def record_for(self, asset: "Asset") -> Record | SourceCard | None:
        """The record that states ``asset``'s terms: by component id, then — for code — by
        the import root, and for a source reference, its card."""
        if asset.kind == "data" and asset.ref.startswith("source:"):
            try:
                key, _ = parse_ref(asset.ref.partition(":")[2])
            except ValueError:
                return None                    # not a source reference: no card states it
            return next((c for c in self.cards if c.key == key), None)
        found = self._by_component.get((asset.kind, asset.ref))
        if found is None and asset.kind == "code":
            found = self._by_module.get(asset.module or asset.ref)
        return found

    # -------------------------------------------------------------- loading
    @classmethod
    def parse(cls, text: str, *, cards: Iterable[SourceCard] = SOURCE_CARDS
              ) -> "LicenceRecords":
        import yaml

        data = yaml.safe_load(text) or {}
        if not isinstance(data, Mapping):
            raise LicenceRecordError("licence_records.yaml must be a mapping")
        unknown = sorted(set(data) - {"api_version", *ASSET_KINDS})
        if unknown:
            raise LicenceRecordError(f"licence_records.yaml has unknown key(s) {unknown}")
        if str(data.get("api_version", "")) != "1":
            raise LicenceRecordError("licence_records.yaml must declare api_version \"1\"")
        parsed = {kind: tuple(_record(kind, entry) for entry in data.get(kind) or ())
                  for kind in ASSET_KINDS}
        return cls(parsed["code"], parsed["model"], parsed["data"], parsed["service"],
                   cards=cards)

    @classmethod
    def load(cls, path: str | Path | None = None, *,
             cards: Iterable[SourceCard] = SOURCE_CARDS) -> "LicenceRecords":
        """The shipped records. A missing file is no records: a commercial run then uses
        nothing but source cards that allow it, and research use is unaffected."""
        target = Path(path) if path is not None else licence_records_path()
        if not target.is_file():
            return cls(cards=cards)
        return cls.parse(target.read_text(encoding="utf-8"), cards=cards)


def _record(kind: str, entry: Any) -> Record:
    klass = _RECORD_TYPES[kind]
    if not isinstance(entry, Mapping):
        raise LicenceRecordError(f"a {kind} record must be a mapping")
    names = {f.name for f in fields(klass)}
    unknown = sorted(set(entry) - names)
    if unknown:
        raise LicenceRecordError(f"{kind} record {entry.get('id')!r} has unknown key(s) "
                                 f"{unknown}")
    kwargs: dict[str, Any] = {}
    for key, value in entry.items():
        if key == "checked":
            if not isinstance(value, Mapping) or set(value) != {"source", "date"}:
                raise LicenceRecordError(f"{kind} record {entry.get('id')!r}: checked is a "
                                         "mapping of source and date")
            value = Checked(str(value["source"]), str(value["date"]))
        elif key == "per_record":
            value = {str(k): {str(a): str(b) for a, b in dict(v).items()}
                     for k, v in dict(value or {}).items()}
        elif key == "first_party":
            if not isinstance(value, bool):
                raise LicenceRecordError(f"{kind} record {entry.get('id')!r}: first_party is "
                                         "true or false")
        elif isinstance(value, list):
            value = tuple(str(v) for v in value)
        else:
            value = str(value)
        kwargs[key] = value
    try:
        return klass(**kwargs)
    except TypeError as exc:                   # a required field is missing
        raise LicenceRecordError(f"{kind} record {entry.get('id')!r}: {exc}") from None


# ---------------------------------------------------------------------- assets

@dataclass(frozen=True)
class Asset:
    """One thing a run uses, described by how it is identified and why it is there."""

    kind: str
    #: A component id, an import name, or ``source:<key>[@version]`` for a source card.
    ref: str
    #: implementation | requirement | returned_data | attached | dependency | source
    role: str
    #: The licence the manifest or the catalogue states, kept so a refusal can say what
    #: was claimed and was not a reviewed grant.
    declared: str = ""
    #: How code is integrated (vendor | native | federated), when it is code.
    mode: str = ""
    #: The import root of a python implementation.
    module: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ASSET_KINDS:
            raise ValueError(f"asset kind {self.kind!r} is not one of {ASSET_KINDS}")


def _implementation(m: Any, role: str) -> Asset:
    """What runs when ``m`` is invoked, by its mechanism: a service behind a connector, a
    dataset that is read, or code — whose import root is what a code record covers."""
    backend, spdx = m.runtime.backend, m.license.spdx
    if m.kind == "connector" or backend in ("http", "mcp"):
        return Asset("service", m.id, role, declared=spdx)
    if m.kind in ("dataset", "database") or backend == "dataset":
        return Asset("data", m.id, role, declared=m.license.data or spdx)
    module = (m.runtime.entrypoint.partition(":")[0].split(".")[0]
              if backend == "python" else "")
    return Asset("code", m.id, role, declared=spdx, mode=m.license.integration_mode,
                 module=module)


def assets_for(m: Any, *, dependencies: Iterable[Any] = (),
               records: LicenceRecords | None = None) -> tuple[Asset, ...]:
    """Everything invoking ``m`` uses: what runs, the third-party code it imports, the data
    it hands back, assets a record attaches to it (model weights, a service it calls), and
    the same for every manifest in its dependency closure."""
    records = records if records is not None else LicenceRecords.load()
    own = _implementation(m, "implementation")
    out = [own]
    out += [Asset("code", module, "requirement") for module in m.requires.python
            if module != own.module]
    if m.license.data:
        out.append(Asset("data", m.id, "returned_data", declared=m.license.data))
    present = {(a.kind, a.ref) for a in out}
    out += [Asset(r.kind, m.id, "attached") for r in records.attached_to(m.id)
            if (r.kind, m.id) not in present]
    out += [_implementation(d, "dependency") for d in dependencies]
    return tuple(dict.fromkeys(out))


# ------------------------------------------------------------------------ gate

@dataclass(frozen=True)
class UsageEntry:
    """One asset, the record relied on, its terms, and the ruling."""

    asset: Asset
    record: str
    terms: Mapping[str, Any]
    ruling: Ruling

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.asset.kind, "ref": self.asset.ref, "role": self.asset.role,
                "declared": self.asset.declared, "mode": self.asset.mode,
                "record": self.record, "terms": dict(self.terms),
                "verdict": self.ruling.decision.value, "reason": self.ruling.reason,
                "rule": self.ruling.rule}


@dataclass(frozen=True)
class UsageDecision:
    """The gate's answer for one use: every asset, and whether the purpose may proceed."""

    purpose: str
    entries: tuple[UsageEntry, ...]

    @property
    def allowed(self) -> bool:
        return all(e.ruling.allowed for e in self.entries)

    @property
    def rulings(self) -> tuple[Ruling, ...]:
        return tuple(e.ruling for e in self.entries)

    @property
    def reason(self) -> str:
        denied = [e.ruling.reason for e in self.entries if not e.ruling.allowed]
        return "; ".join(denied) if denied else (
            f"every asset this use draws on permits {self.purpose} use")

    def authorization(self, auth: Authorization) -> Authorization:
        """``auth`` with these rulings added: denied if either the kernel or the gate is."""
        return Authorization(component_id=auth.component_id,
                             allowed=auth.allowed and self.allowed,
                             rulings=tuple(auth.rulings) + self.rulings)

    def as_dict(self) -> dict[str, Any]:
        return {"purpose": self.purpose, "verdict": "allow" if self.allowed else "deny",
                "reason": self.reason, "assets": [e.as_dict() for e in self.entries]}


def _card_ruling(card: SourceCard, asset: Asset, purpose: str) -> Ruling:
    """A source card is ruled by ``effective_sources`` itself, so the two cannot differ."""
    ref = asset.ref.partition(":")[2]
    granted, refused = effective_sources([ref], cards=[card], purpose=purpose)
    if granted:
        return Ruling(PolicyDecision.ALLOW, f"{card.name} ({card.license}) allows "
                      f"{purpose} use", "usage.data.source_card")
    return Ruling(PolicyDecision.DENY, f"{card.name}: {refused[ref]}",
                  "usage.data.source_card")


def _terms(record: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(record, SourceCard):
        return f"source:{record.key}", {
            "licence": record.license, "terms_url": record.terms_url,
            "per_record": {k: dict(v) for k, v in record.per_record_license.items()},
            "commercial_use": record.commercial_use}
    if record is None:
        return "", {}
    return record.id, record.terms()


def usage_decision(assets: Iterable[Asset], *, purpose: str,
                   records: LicenceRecords | None = None) -> UsageDecision:
    """Rule on every asset of one use for ``purpose`` (one of ``sources.cards.PURPOSES``).

    ``commercial``: each asset needs a record whose terms permit commercial use; one
    without is refused with the licence it merely declared. ``academic``: no licence
    record refuses anything, a source card answers as ``effective_sources`` does for
    research, and the records found are still returned for the provenance.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"purpose {purpose!r} is not one of {PURPOSES}")
    records = records if records is not None else LicenceRecords.load()
    entries = []
    for asset in dict.fromkeys(assets):
        record = records.record_for(asset)
        record_id, terms = _terms(record)
        if isinstance(record, SourceCard):
            ruling = _card_ruling(record, asset, purpose)
        elif purpose != "commercial":
            ruling = Ruling(PolicyDecision.ALLOW,
                            f"{asset.kind} {asset.ref}: {purpose} use is ruled on by the "
                            "policy kernel and the source cards, not by licence records",
                            f"usage.{purpose}")
        elif record is None:
            claimed = (f"; it declares {asset.declared!r}, which is a statement, not a "
                       "reviewed grant" if asset.declared else "")
            ruling = Ruling(PolicyDecision.DENY,
                            f"{asset.kind} {asset.ref} ({asset.role}) has no reviewed "
                            f"licence record, and unknown terms are not permission for "
                            f"commercial use{claimed}", f"usage.{asset.kind}.unknown")
        else:
            ruling = record.commercial_ruling(asset)
        entries.append(UsageEntry(asset, record_id, terms, ruling))
    return UsageDecision(purpose, tuple(entries))
