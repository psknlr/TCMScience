"""What a falsifiable study is about, and what it measured, kept apart from what it infers.

Six records, each a plain frozen dataclass:

* ``InterventionSpec``: the exact thing given — a compound, a defined mixture, a formula
  version, or a preparation. A formula *name* is not an intervention: 葛根芩连汤 of 伤寒论
  (four herbs) and a trial's seven-herb "葛根芩连汤" are two interventions, and
  ``same_as`` says so.
* ``PreparationBatch``: source, part, processing, extraction, co-decoction or mixing,
  and the batch, so preparation can be a variable rather than an assumption.
* ``Measurement``: one value with its qualifier. "Not tested", "below the limit of
  detection", "above the highest concentration tested" and "tested, inactive" are
  different facts and never become 0 or blank.
* ``ExposureRecord``, ``AssayResult``, ``PerturbationContrast``: an observed exposure, an
  assay outcome, a perturbation-vs-control contrast, each with its context and the study it
  came from.
* ``StudyProtocol``: hypothesis, competing explanations, primary endpoint, analysis
  partition, decision rules. ``lock()`` fixes it by hash; a change after the lock is an
  amendment that makes the analysis exploratory. A confirmatory result must cite the
  locked hash it was run under.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping

__all__ = ["InterventionSpec", "PreparationBatch", "Measurement", "QUALIFIERS",
           "ExposureRecord", "AssayResult", "PerturbationContrast", "StudyProtocol",
           "ProtocolError", "fingerprint"]


def fingerprint(obj: Any) -> str:
    blob = json.dumps(obj if isinstance(obj, (dict, list)) else asdict(obj),
                      ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ interventions
KINDS = ("compound", "mixture", "formula", "preparation")


@dataclass(frozen=True)
class InterventionSpec:
    """An intervention by its exact composition.

    ``components``: (identity, role, amount, unit, processing) per component. Identity is
    a compound id (``inchikey:``/``pubchem:``) for compounds, a crude-drug id
    (``tcm:herb.…``) for herbs. ``source`` is the text or study the composition is taken
    from; ``preparation`` names a ``PreparationBatch`` when the batch is known.
    """

    kind: str
    name: str
    components: tuple[tuple[str, str, str, str, str], ...]
    source: str = ""
    preparation: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"intervention kind {self.kind!r} is not one of {KINDS}")
        if not self.components:
            raise ValueError("an intervention names what it contains")
        if self.kind == "compound" and len(self.components) != 1:
            raise ValueError("a compound intervention has exactly one component")

    @property
    def identities(self) -> frozenset[str]:
        return frozenset(c[0] for c in self.components)

    @property
    def fingerprint(self) -> str:
        return fingerprint({"kind": self.kind, "components": sorted(self.components),
                            "preparation": self.preparation})

    def same_as(self, other: "InterventionSpec") -> bool:
        """Same composition, amounts and preparation, whatever the names."""
        return self.fingerprint == other.fingerprint

    def differences(self, other: "InterventionSpec") -> dict[str, list[str]]:
        mine, theirs = self.identities, other.identities
        return {"only_here": sorted(mine - theirs), "only_there": sorted(theirs - mine),
                "amount_or_processing": sorted(
                    c[0] for c in self.components for d in other.components
                    if c[0] == d[0] and c[2:] != d[2:])}

    @classmethod
    def from_formula(cls, formula: Any) -> "InterventionSpec":
        """From a ``sources.herbs.FormulaVersion``."""
        return cls("formula", formula.chinese,
                   tuple((h, role, dose, "", proc) for h, role, dose, proc in formula.components),
                   source=formula.source)

    @classmethod
    def compound(cls, identity: str, name: str, *, amount: str = "", unit: str = "",
                 source: str = "") -> "InterventionSpec":
        return cls("compound", name, ((identity, "", amount, unit, ""),), source=source)


@dataclass(frozen=True)
class PreparationBatch:
    id: str
    intervention: str                    # InterventionSpec fingerprint
    material_source: str = ""            # origin, supplier, collection
    part: str = ""
    processing: str = ""                 # 炮制
    extraction: str = ""                 # solvent, ratio, time, temperature
    combination: str = ""                # co-decoction | separate extraction then mixing | …
    batch: str = ""
    chemistry: Mapping[str, "Measurement"] = field(default_factory=dict)


# ------------------------------------------------------------------ measurements
QUALIFIERS = ("measured", "below_lod", "above_max_tested", "inactive", "not_tested",
              "inconclusive")


@dataclass(frozen=True)
class Measurement:
    """One value and what it means. ``value`` is set only when ``qualifier == 'measured'``;
    ``bound`` holds the limit for below_lod / above_max_tested."""

    qualifier: str
    value: float | None = None
    unit: str = ""
    bound: float | None = None

    def __post_init__(self) -> None:
        if self.qualifier not in QUALIFIERS:
            raise ValueError(f"qualifier {self.qualifier!r} is not one of {QUALIFIERS}")
        if (self.qualifier == "measured") != (self.value is not None):
            raise ValueError("a value is given exactly when the qualifier is 'measured'")
        if self.qualifier in ("below_lod", "above_max_tested") and self.bound is None:
            raise ValueError(f"{self.qualifier} needs the bound it refers to")

    @property
    def known(self) -> bool:
        return self.qualifier == "measured"


@dataclass(frozen=True)
class ExposureRecord:
    molecule: str                        # parent or metabolite id
    species: str
    site: str                            # plasma | tissue:<uberon> | gut_lumen | …
    time_h: float | None
    concentration: Measurement           # total unless ``free`` is True
    free: bool = False
    free_fraction: tuple[float, float] | None = None   # measured range, if any
    method: str = ""
    intervention: str = ""               # InterventionSpec fingerprint
    dose: str = ""
    study: str = ""                      # pmid:/doi:/dataset id


@dataclass(frozen=True)
class AssayResult:
    molecule: str
    target: str
    endpoint: str                        # IC50 | EC50 | Ki | Kd | AC50 | percent_inhibition | …
    result: Measurement
    species: str = ""
    system: str = ""                     # cell line, enzyme prep, …
    method: str = ""
    flags: tuple[str, ...] = ()          # aggregation, autofluorescence, cytotoxicity, …
    study: str = ""


@dataclass(frozen=True)
class PerturbationContrast:
    perturbation: str                    # intervention fingerprint or a gene perturbation
    control: str
    context: Mapping[str, str]           # cell, tissue, dose, time, …
    readout: str
    estimate: float | None
    se: float | None
    n_independent: int                   # biological replicates, not cells
    study: str = ""


# ------------------------------------------------------------------ protocol
class ProtocolError(RuntimeError):
    """An analysis that does not match its locked protocol."""


@dataclass(frozen=True)
class StudyProtocol:
    title: str
    question: str
    hypothesis: str
    competing: tuple[str, ...]           # explanations that would produce the same data
    primary_endpoint: str
    analysis: Mapping[str, Any]          # model, margin, reference model, partition, alpha …
    decision_rules: Mapping[str, str]    # outcome -> what may be concluded
    interventions: tuple[str, ...] = ()  # fingerprints
    data: tuple[str, ...] = ()           # dataset / snapshot ids
    locked: str = ""                     # hash once locked
    amendments: tuple[Mapping[str, Any], ...] = ()

    def _content(self) -> dict:
        d = asdict(self)
        d.pop("locked")
        d.pop("amendments")
        return d

    def lock(self) -> "StudyProtocol":
        if not self.competing:
            raise ProtocolError("a protocol names at least one competing explanation")
        if not self.decision_rules:
            raise ProtocolError("a protocol says in advance what each outcome allows")
        return replace(self, locked=fingerprint(self._content()))

    @property
    def confirmatory(self) -> bool:
        return bool(self.locked) and not self.amendments \
            and self.locked == fingerprint(self._content())

    def amend(self, reason: str, **changes: Any) -> "StudyProtocol":
        """A change after locking: recorded, and the analysis becomes exploratory."""
        if not self.locked:
            return replace(self, **changes)
        return replace(self, **changes,
                       amendments=self.amendments + ({"reason": reason,
                                                      "changed": sorted(changes),
                                                      "from": self.locked},))

    def check(self, *, endpoint: str, analysis: Mapping[str, Any]) -> str:
        """``confirmatory`` or ``exploratory`` for an analysis about to run; raises when the
        protocol is locked and the analysis silently departs from it."""
        if not self.locked:
            return "exploratory"
        if self.amendments:
            return "exploratory"
        if endpoint != self.primary_endpoint:
            raise ProtocolError(f"the locked primary endpoint is {self.primary_endpoint!r}, "
                                f"not {endpoint!r}; amend the protocol (the analysis then "
                                "becomes exploratory)")
        departures = {k for k in self.analysis if analysis.get(k) != self.analysis[k]}
        if departures:
            raise ProtocolError(f"the analysis departs from the locked protocol on "
                                f"{sorted(departures)}")
        return "confirmatory"
