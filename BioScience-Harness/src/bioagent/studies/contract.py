"""The study contract: what is asked, of which units, measured how, and checked against what.

The contract is written before any tool runs, and the analysis is held to it. It keeps two
designs apart that are easy to blur:

* ``OriginalDesign``: how the data were generated (randomised or not, what one sample
  is, pooled libraries, whole transcriptome or a targeted panel). It is a fact about the
  source and bounds every claim made from it; the re-analysis cannot change it.
* ``StudySpec`` with its ``ContrastSpec``s and ``ValidationPlan``: the design of *this*
  analysis (question, unit of inference, contrasts, test families, discovery and
  validation sets, what result would refute the hypothesis).

It also keeps two evidence chains apart. A *disease* claim ("cell state X is altered in
ulcerative colitis") and an *intervention* claim ("a constituent of the formula perturbs
X at reached exposure") are separate records. A *bridge* claim ("the formula acts through
X") needs both, so finding a disease target never by itself shows that a herb acts on it.

The objects:

* ``CohortManifest`` of ``Sample``s: one row per measured sample, with the subject it
  came from, dataset, condition, time, batch, role (discovery / validation / sensitivity)
  and the original study, so independence, pairing, leakage and duplication can be checked.
* ``ContrastSpec``: one comparison, its unit (subject, never cell), pairing and test family.
* ``OmicsArtifact``: one data object, with whether it was measured, imputed or predicted,
  and its measured feature space (a 1,000-gene panel is not a transcriptome).
* ``ValidationPlan``: discovery and validation sets, the split unit, what was selected on
  which data, the test families and the stop rules.
* ``ClaimRecord``: a conclusion, the chain it belongs to, the evidence it rests on, and
  every downgrade with its reason.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .design import StudyProtocol, fingerprint

__all__ = ["QUESTION_TYPES", "CHAINS", "CLAIM_LEVELS", "ROLES", "PROVENANCE",
           "OriginalDesign", "Sample", "CohortManifest", "ContrastSpec", "OmicsArtifact",
           "ValidationPlan", "StudySpec", "ClaimRecord", "assess_bridge"]

# The research routes a study can take (the question decides the tools, not the reverse).
QUESTION_TYPES = {
    "composition_vs_state": "is a bulk signal a change in which cells are present, or in "
                            "what the same cells do?",
    "spatial_neighbourhood": "do cell types sit together more (or less) in disease, "
                             "judged per patient?",
    "inflammation_vs_repair": "does a signal go beyond inflammation resolving, i.e. "
                              "repair or residual damage at matched inflammation?",
    "genetic_target": "is a target supported by tissue- and cell-specific genetics "
                      "(eQTL, colocalisation), not only by association?",
    "isoform": "does the disease or treatment change splicing or isoform use?",
    "microbial_function": "is a microbial function present (DNA), expressed (RNA) or "
                          "reflected in metabolites, in the same sample?",
    "multiomics_discordance": "where do layers measured on the same visit disagree?",
    "perturbation_transfer": "does a perturbation signature transfer to the disease "
                             "context better than simple baselines?",
    "disease_specificity": "is a signal specific to this disease or shared with others?",
    "response_prediction": "does information at baseline predict a later outcome beyond "
                           "baseline severity?",
}

CHAINS = ("disease", "intervention", "bridge")
ROLES = ("discovery", "validation", "sensitivity", "")
PROVENANCE = ("measured", "imputed", "predicted", "derived")

# Ordered: a claim can be capped down this ladder, never pushed up by a cap.
CLAIM_LEVELS = ("not_supported", "exploratory", "association", "validated_association",
                "experimentally_supported", "causal")


@dataclass(frozen=True)
class OriginalDesign:
    """How the source data were generated. A fact about the source, not a choice."""

    accession: str
    design: str                       # rct, cohort, case_control, cross_sectional, …
    randomized: bool = False
    blinded: bool = False
    sample_unit: str = "biopsy"       # biopsy, stool, blood, pooled_library, cell, spot
    pooled: bool = False              # one GEO "sample" holds several people
    outcome_definition: str = ""
    timepoints: tuple[str, ...] = ()
    n_subjects_reported: int | None = None
    notes: tuple[str, ...] = ()

    def causal_ceiling(self) -> str:
        """The highest claim level the design itself supports: without randomisation a
        treatment effect stays an association, however well it replicates."""
        return "causal" if self.randomized else "validated_association"


@dataclass(frozen=True)
class Sample:
    sample_id: str
    subject_id: str
    dataset: str
    condition: str = ""
    timepoint: str = ""               # label (W0, W12, visit 3); days in attrs['day']
    batch: str = ""
    tissue: str = ""
    role: str = ""
    original_study: str = ""
    attrs: Mapping[str, str] = field(default_factory=dict)

    def get(self, name: str, default: str = "") -> str:
        if name in _CORE:
            return str(getattr(self, name))
        return str(self.attrs.get(name, default))


_CORE = ("sample_id", "subject_id", "dataset", "condition", "timepoint", "batch", "tissue",
         "role", "original_study")


class CohortManifest:
    """The sample table. Every analysis reads units, pairs and roles from here."""

    def __init__(self, samples: Iterable[Sample]):
        self.samples: tuple[Sample, ...] = tuple(samples)
        ids = [s.sample_id for s in self.samples]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"sample ids repeat in the manifest: {dup[:5]}")
        bad = sorted({s.role for s in self.samples} - set(ROLES))
        if bad:
            raise ValueError(f"unknown sample roles {bad}; use one of {ROLES}")

    def __len__(self) -> int:
        return len(self.samples)

    def __iter__(self):
        return iter(self.samples)

    def by_id(self) -> dict[str, Sample]:
        return {s.sample_id: s for s in self.samples}

    def where(self, **match: str) -> "CohortManifest":
        return CohortManifest(s for s in self.samples
                              if all(s.get(k) == str(v) for k, v in match.items()))

    def subjects(self) -> list[str]:
        return sorted({s.subject_id for s in self.samples if s.subject_id})

    def columns(self) -> list[str]:
        extra = sorted({k for s in self.samples for k in s.attrs})
        return list(_CORE) + extra

    def to_tsv(self) -> str:
        cols = self.columns()
        out = io.StringIO()
        w = csv.writer(out, delimiter="\t", lineterminator="\n")
        w.writerow(cols)
        for s in self.samples:
            w.writerow([s.get(c) for c in cols])
        return out.getvalue()

    @classmethod
    def from_tsv(cls, text: str) -> "CohortManifest":
        rows = csv.DictReader(io.StringIO(text), delimiter="\t")
        samples = []
        for r in rows:
            core = {k: r.pop(k, "") or "" for k in _CORE}
            samples.append(Sample(**core, attrs={k: v for k, v in r.items() if v != ""}))
        return cls(samples)

    @classmethod
    def read(cls, path: str | Path) -> "CohortManifest":
        return cls.from_tsv(Path(path).read_text(encoding="utf-8"))

    def fingerprint(self) -> str:
        return fingerprint(self.to_tsv())


@dataclass(frozen=True)
class ContrastSpec:
    """One comparison. ``variable`` is a manifest column; levels are compared per ``unit``."""

    id: str
    variable: str
    case: str
    control: str
    unit: str = "subject_id"           # the independent unit; a cell is never one
    paired: bool = False               # same subject measured under both levels
    family: str = "primary"            # the test family it is corrected within
    where: Mapping[str, str] = field(default_factory=dict)   # restrict samples first
    covariates: tuple[str, ...] = ()
    description: str = ""

    def select(self, manifest: CohortManifest) -> dict[str, list[Sample]]:
        """Samples of each level after the ``where`` restriction."""
        chosen = manifest.where(**self.where) if self.where else manifest
        return {lvl: [s for s in chosen if s.get(self.variable) == lvl]
                for lvl in (self.case, self.control)}

    def units(self, manifest: CohortManifest) -> dict[str, list[str]]:
        sel = self.select(manifest)
        return {lvl: sorted({s.get(self.unit) for s in v if s.get(self.unit)})
                for lvl, v in sel.items()}

    def as_row(self) -> dict[str, str]:
        return {"id": self.id, "variable": self.variable, "case": self.case,
                "control": self.control, "unit": self.unit, "paired": str(self.paired).lower(),
                "family": self.family,
                "where": ";".join(f"{k}={v}" for k, v in sorted(self.where.items())),
                "covariates": ",".join(self.covariates), "description": self.description}

    @classmethod
    def from_row(cls, r: Mapping[str, str]) -> "ContrastSpec":
        where = dict(kv.split("=", 1) for kv in r.get("where", "").split(";") if kv)
        return cls(id=r["id"], variable=r["variable"], case=r["case"], control=r["control"],
                   unit=r.get("unit") or "subject_id",
                   paired=r.get("paired", "false").lower() == "true",
                   family=r.get("family") or "primary", where=where,
                   covariates=tuple(c for c in r.get("covariates", "").split(",") if c),
                   description=r.get("description", ""))


def contrasts_tsv(contrasts: Sequence[ContrastSpec]) -> str:
    cols = ["id", "variable", "case", "control", "unit", "paired", "family", "where",
            "covariates", "description"]
    out = io.StringIO()
    w = csv.DictWriter(out, cols, delimiter="\t", lineterminator="\n")
    w.writeheader()
    for c in contrasts:
        w.writerow(c.as_row())
    return out.getvalue()


def read_contrasts(text: str) -> list[ContrastSpec]:
    return [ContrastSpec.from_row(r) for r in csv.DictReader(io.StringIO(text), delimiter="\t")]


__all__ += ["contrasts_tsv", "read_contrasts"]


@dataclass(frozen=True)
class OmicsArtifact:
    """One data object and what it can stand for."""

    id: str
    modality: str                    # bulk_rna, scrna, spatial, metagenome,
    #                                  metatranscriptome, metabolome, eqtl, structure, …
    level: str = "sample"            # cell, spot, sample, subject
    provenance: str = "measured"     # measured, imputed, predicted, derived
    feature_space: str = "whole_transcriptome"   # whole_transcriptome, targeted_panel,
    #                                  genome, untargeted, targeted
    n_features: int | None = None
    features: tuple[str, ...] = ()   # the measured features when the space is a panel
    annotations: tuple[str, ...] = ()    # cell_type, coordinates, isoform, junctions, …
    protocol: str = ""               # e.g. 10x 3' v3, Visium, Xenium, MERFISH, Affymetrix
    accession: str = ""
    path: str = ""
    checksum: str = ""
    samples: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.provenance not in PROVENANCE:
            raise ValueError(f"provenance is one of {PROVENANCE}")
        if self.feature_space == "targeted_panel" and not (self.features or self.n_features):
            raise ValueError("a targeted panel records its measured features (or their "
                             "number): they are the background of every enrichment")

    @property
    def measured(self) -> bool:
        return self.provenance == "measured"

    def background(self) -> tuple[str, ...] | None:
        """The feature universe an enrichment may use; ``None`` for a whole-space assay."""
        return self.features if self.feature_space == "targeted_panel" else None


@dataclass(frozen=True)
class ValidationPlan:
    discovery: tuple[str, ...]                     # dataset ids
    validation: tuple[str, ...] = ()
    split_unit: str = "subject_id"
    selection_datasets: tuple[str, ...] = ()       # data used to choose features/thresholds
    frozen_selection: str = ""                     # fingerprint, recorded before validation
    families: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    alpha: float = 0.05
    min_subjects_per_arm: int = 5
    windows: Mapping[str, tuple[float, float]] = field(default_factory=dict)   # days
    stop_rules: tuple[str, ...] = ()

    def family_of(self, contrast_id: str) -> str | None:
        for fam, ids in self.families.items():
            if contrast_id in ids:
                return fam
        return None

    def freeze(self, selection: Any) -> "ValidationPlan":
        """Record what discovery selected, before validation data are opened."""
        return replace(self, frozen_selection=fingerprint(selection))


@dataclass(frozen=True)
class StudySpec:
    id: str
    title: str
    question: str
    question_type: str
    chain: str                          # disease, intervention, bridge
    hypothesis: str
    falsifier: str                      # the result that would refute the hypothesis
    competing: tuple[str, ...]
    primary_contrast: str
    unit_of_inference: str = "subject_id"
    datasets: Mapping[str, str] = field(default_factory=dict)    # dataset -> role
    original: tuple[OriginalDesign, ...] = ()
    decision_rules: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.question_type not in QUESTION_TYPES:
            raise ValueError(f"unknown question type {self.question_type!r}; one of "
                             f"{sorted(QUESTION_TYPES)}")
        if self.chain not in CHAINS:
            raise ValueError(f"chain is one of {CHAINS}")
        if not self.falsifier:
            raise ValueError("a study states in advance the result that would refute it")

    def protocol(self, contrasts: Sequence[ContrastSpec], plan: ValidationPlan, *,
                 data: Sequence[str] = ()) -> StudyProtocol:
        """The hash-lockable protocol: contrasts and the validation plan are its analysis."""
        return StudyProtocol(
            title=self.title, question=self.question, hypothesis=self.hypothesis,
            competing=self.competing, primary_endpoint=self.primary_contrast,
            analysis={"contrasts": fingerprint([c.as_row() for c in contrasts]),
                      "validation_plan": fingerprint(asdict(plan)),
                      "unit": self.unit_of_inference, "alpha": plan.alpha},
            decision_rules=dict(self.decision_rules) or {"refuted": self.falsifier},
            data=tuple(data) or tuple(sorted(self.datasets)))

    def ceiling(self) -> str:
        """The lowest ceiling among the source designs (no recorded source: association)."""
        if not self.original:
            return "association"
        return min((o.causal_ceiling() for o in self.original), key=CLAIM_LEVELS.index)


@dataclass(frozen=True)
class ClaimRecord:
    id: str
    statement: str
    chain: str
    level: str
    contrasts: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    validated_in: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    downgrades: tuple[Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.chain not in CHAINS:
            raise ValueError(f"chain is one of {CHAINS}")
        if self.level not in CLAIM_LEVELS:
            raise ValueError(f"level is one of {CLAIM_LEVELS}")

    def cap(self, level: str, reason: str) -> "ClaimRecord":
        """Lower the claim to at most ``level``; a cap above the current level is a no-op."""
        if CLAIM_LEVELS.index(level) >= CLAIM_LEVELS.index(self.level):
            return self
        return replace(self, level=level, downgrades=self.downgrades + (
            {"from": self.level, "to": level, "reason": reason},))

    def as_dict(self) -> dict:
        d = asdict(self)
        d["downgrades"] = [dict(x) for x in self.downgrades]
        return d


def assess_bridge(statement: str, disease: ClaimRecord | None,
                  intervention: ClaimRecord | None, *, claim_id: str = "bridge") -> ClaimRecord:
    """A claim that an intervention acts through a disease feature needs both chains.

    The bridge is no stronger than its weaker chain, and never above ``association`` unless
    the intervention evidence is experimental in a relevant context.
    """
    rec = ClaimRecord(claim_id, statement, "bridge", "experimentally_supported",
                      evidence=tuple(c.id for c in (disease, intervention) if c))
    if disease is None or disease.chain != "disease":
        return rec.cap("not_supported", "no disease-chain claim: the feature is not shown "
                                        "to be altered in the disease")
    if intervention is None or intervention.chain != "intervention":
        return rec.cap("not_supported", "a disease target alone does not show that the "
                                        "intervention acts through it; no intervention-"
                                        "chain evidence")
    weaker = min(disease.level, intervention.level, key=CLAIM_LEVELS.index)
    rec = rec.cap(weaker, f"no stronger than its weaker chain ({weaker})")
    if intervention.level != "experimentally_supported":
        rec = rec.cap("association", "the intervention evidence is not experimental in a "
                                     "relevant context")
    return rec
